"""
趋势跟踪策略 — AI 判断方向 + 规则引擎执行
从 ai-hedge-fund-crypto 迁移
"""
import logging
from typing import Optional

from src.strategies.base import BaseStrategy
from src.strategies.trend_following.rules import RuleEngine
from src.core.context import SharedContext, TradeSignal, SmartMoneyData
from src.utils.constants import Direction

logger = logging.getLogger("unified_trader.strategy.trend_following")


class TrendFollowingStrategy(BaseStrategy):
    """AI+规则混合趋势跟踪策略 — 单币种多时间框架"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.rule_engine = RuleEngine(config)
        self._tickers: list[str] = []

    @property
    def name(self) -> str:
        return "trend_following"

    def get_interval(self) -> int:
        # 30分钟周期
        call_interval = self.config.get("ai", {}).get("call_interval", 30)
        return call_interval * 60

    def get_symbols(self, ctx: SharedContext) -> list[str]:
        if not self._tickers:
            self._tickers = self.config.get("tickers", ["AVAXUSDT"])
        return self._tickers

    def on_cycle(self, ctx: SharedContext) -> list[TradeSignal]:
        signals = []

        for sym in self.get_symbols(ctx):
            market = ctx.get_market(sym)
            if not market:
                continue

            sm = ctx.get_smart_money(sym)

            # 收集市场指标
            indicators = self._collect_indicators(market, sm)

            # 调用规则引擎
            direction, score, details = self.rule_engine.evaluate(
                ctx, sym,
                ai_direction=self._get_ai_direction(ctx, sym),
                ai_confidence=50.0,  # 默认，实际由 AI 客户端覆盖
                market_indicators=indicators,
            )

            if direction != Direction.NEUTRAL:
                sig = TradeSignal(
                    symbol=sym,
                    direction=direction,
                    score=score,
                    sub_scores=details,
                    reason=f"composite={score:.1f}",
                    source=self.name,
                    features={
                        "indicators": indicators,
                        "ai_confidence": 50.0,
                    },
                )
                signals.append(sig)

                logger.info(
                    "[%s] AI+规则 %s | score=%.1f | %s",
                    sym, direction.value, score,
                    ", ".join(f"{k}={v:.0f}" for k, v in details.items()
                             if k != "composite"),
                )

        return signals

    def on_position_closed(self, pos, pnl: float) -> None:
        self.rule_engine.on_trade_closed(pnl)

    def get_risk_profile(self) -> dict:
        return {
            "max_positions": 1,
            "order_usdt_per_symbol": self.config.get("order_usdt_per_symbol", 15),
            "leverage": self.config.get("leverage", 3),
            "stop_loss_pct": self.config.get("stop_loss_pct", 15.0),
            "take_profit_pct": self.config.get("take_profit_pct", 10.0),
            "trailing_stop_activate_pct": self.config.get("trailing_stop_activate_pct", 3.0),
            "trailing_stop_callback_pct": self.config.get("trailing_stop_callback_pct", 7.0),
            "min_hold_minutes": self.config.get("min_hold_minutes", 60),
        }

    # ═══════ 指标采集 ═══════

    def _collect_indicators(self, market, sm: Optional[SmartMoneyData]) -> dict:
        """采集市场指标"""
        ind = {}

        # Taker 买卖比
        if market.taker_sell_volume_5min > 0:
            ind["taker_ratio"] = market.taker_buy_volume_5min / market.taker_sell_volume_5min
        else:
            ind["taker_ratio"] = 1.0

        # Smart Money 对齐度：0-100
        if sm:
            sm_bullish = (
                sm.top_account_ratio > 1.0 and
                sm.taker_buy_sell_ratio > 1.0
            )
            ind["sm_alignment"] = 75.0 if sm_bullish else 25.0
        else:
            ind["sm_alignment"] = 50.0

        # 默认中性值
        ind.setdefault("macd_signal", 0.0)
        ind.setdefault("rsi", 50.0)

        return ind

    def _get_ai_direction(self, ctx: SharedContext, symbol: str) -> str:
        """
        AI 判断方向 — 简化版（无 LLM 调用）

        TODO Phase 5: 接入 Qwen API
        当前用 SmartMoney 数据做代理
        """
        sm = ctx.get_smart_money(symbol)
        if sm:
            if sm.top_account_ratio > 1.2 and sm.taker_buy_sell_ratio > 1.1:
                return "bullish"
            elif sm.top_account_ratio < 0.8 and sm.taker_buy_sell_ratio < 0.9:
                return "bearish"
        return "neutral"
