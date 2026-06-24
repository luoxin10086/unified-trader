"""
新币策略 — 监控最新 N 个币种，纯规则做多
"""
import logging
import time
from typing import Optional

from src.strategies.base import BaseStrategy
from src.strategies.new_coin.analyzer import NewCoinAnalyzer, AnalysisResult
from src.strategies.new_coin.scanner import NewCoinScanner
from src.core.context import SharedContext, TradeSignal
from src.utils.constants import Direction

logger = logging.getLogger("unified_trader.strategy.new_coin")


class NewCoinStrategy(BaseStrategy):
    """新币种主力资金跟踪策略 — 只做多"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.scanner: Optional[NewCoinScanner] = None
        self.analyzer: Optional[NewCoinAnalyzer] = None
        self._symbols: list[str] = []
        self._scan_count: int = 0
        self._btc_price_5m_ago: float = 0.0
        self._btc_ema: float = 0.0
        self._btc_trend_bias: float = 0.0
        self._oi_snapshots: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "new_coin"

    def get_interval(self) -> int:
        return self.config.get("loop_interval_seconds", 30)

    def get_symbols(self, ctx: SharedContext) -> list[str]:
        return list(self._symbols)

    def on_start(self, ctx: SharedContext) -> None:
        self.scanner = NewCoinScanner(None, self.config)  # REST client will be injected
        self.analyzer = NewCoinAnalyzer(self.config)
        super().on_start(ctx)

    def set_rest_client(self, rest) -> None:
        """由引擎注入 REST 客户端"""
        if self.scanner:
            self.scanner.rest = rest

    # ═══════ 主周期 ═══════

    def on_cycle(self, ctx: SharedContext) -> list[TradeSignal]:
        self._scan_count += 1

        signals = []

        # 1. 刷新币种列表（每 10 轮或首次）
        if self.scanner and (self._scan_count % 10 == 1 or not self._symbols):
            self._refresh_symbols()

        # 2. 更新 BTC 趋势
        self._update_btc_trend(ctx)

        # 3. 扫描每个币种
        for sym in self._symbols:
            market = ctx.get_market(sym)
            if not market or not market.trades:
                continue

            # OI 变化
            oi_delta_pct = self._get_oi_delta(sym, market.open_interest)

            # BTC 变化
            btc_change = self._get_btc_change(ctx)

            # 分析
            result = self.analyzer.analyze(
                sym, ctx,
                btc_change_pct=btc_change,
                btc_trend_bias=self._btc_trend_bias,
                oi_delta_pct=oi_delta_pct,
            )

            # 转换为 TradeSignal
            if result.direction != Direction.NEUTRAL:
                sig = TradeSignal(
                    symbol=sym,
                    direction=result.direction,
                    score=result.score,
                    sub_scores={
                        "flow": result.flow_score,
                        "pattern": result.pattern_score,
                    },
                    reason=result.reason,
                    source=self.name,
                    features={
                        "net_flow": result.net_flow,
                        "large_trade_count": result.large_trade_count,
                        "large_buy_vol": result.large_buy_vol,
                        "large_sell_vol": result.large_sell_vol,
                        "buy_ratio": result.buy_ratio,
                        "window_flow_details": result.window_flow_details,
                        "triggered_patterns": result.triggered_patterns,
                        "feature_scores": result.feature_scores,
                        "btc_change_pct": btc_change,
                        "btc_trend_bias": self._btc_trend_bias,
                        "oi_delta_pct": oi_delta_pct,
                    },
                )
                signals.append(sig)

                logger.info(
                    "[%s] %s | score=%.1f flow=%.0f pattern=%.0f | %s",
                    sym, result.direction.value,
                    result.score, result.flow_score, result.pattern_score,
                    "+".join(result.triggered_patterns[:3]) if result.triggered_patterns else "无形态",
                )

        return signals

    def on_position_closed(self, pos, pnl: float) -> None:
        """平仓后重置该币种冷却"""
        if self.analyzer:
            self.analyzer.reset_cooldown(pos.symbol)

    def get_risk_profile(self) -> dict:
        return {
            "max_positions": self.config.get("max_positions", 3),
            "order_usdt_per_symbol": self.config.get("order_usdt_per_symbol", 10),
            "leverage": self.config.get("leverage", 3),
            "stop_loss_pct": self.config.get("stop_loss_pct", 16.7),
            "take_profit_pct": self.config.get("take_profit_pct", 20.0),
            "trailing_stop_activate_pct": self.config.get("trailing_stop_activate_pct", 7.0),
            "trailing_stop_callback_pct": self.config.get("trailing_stop_callback_pct", 5.0),
            "min_hold_minutes": self.config.get("min_hold_minutes", 120),
        }

    # ═══════ 辅助 ═══════

    def _refresh_symbols(self) -> None:
        """刷新币种列表"""
        if not self.scanner:
            return
        try:
            symbols = self.scanner.get_new_symbols()
            symbols = self.scanner.filter_by_price_rise(symbols)

            # 成交量激增补充
            if self.scanner.vol_surge_enabled:
                self.scanner.poll_24h_volumes(symbols)
                surge = self.scanner.get_volume_surge_symbols()
                all_syms = list(dict.fromkeys(symbols + surge))
            else:
                all_syms = symbols

            if set(all_syms) != set(self._symbols):
                logger.info("币种变更: %d → %d", len(self._symbols), len(all_syms))
                self._symbols = all_syms
        except Exception as e:
            logger.warning("刷新币种失败: %s", e)

    def _update_btc_trend(self, ctx: SharedContext) -> None:
        """更新 BTC 趋势"""
        market = ctx.get_market("BTCUSDT")
        if not market:
            return

        btc_price = market.mark_price or 0
        if btc_price <= 0:
            return

        # 价格变化
        btc_5m = market.price_history.get(300, 0)
        if btc_5m > 0 and btc_price > 0:
            self._btc_price_5m_ago = btc_5m

        # EMA 趋势（简化：用价格位置判断）
        # TODO Phase 5: 计算真正的 EMA50
        self._btc_trend_bias = 0  # placeholder

    def _get_btc_change(self, ctx: SharedContext) -> float:
        """BTC 5分钟变化率"""
        market = ctx.get_market("BTCUSDT")
        if not market:
            return 0.0
        price_5m = market.price_history.get(300, 0)
        current = market.mark_price or 0
        if price_5m > 0 and current > 0:
            return (current - price_5m) / price_5m * 100
        return 0.0

    def _get_oi_delta(self, symbol: str, current_oi: float) -> float:
        """OI 变化率"""
        prev = self._oi_snapshots.get(symbol, 0)
        self._oi_snapshots[symbol] = current_oi
        if prev > 0 and current_oi > 0:
            return (current_oi - prev) / prev * 100
        return 0.0
