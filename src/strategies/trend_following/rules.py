"""
规则引擎 — 从 ai-hedge-fund-crypto 迁移

5因子加权评分系统：AI(50%) + MACD(25%) + Taker(10%) + SmartMoney(10%) + RSI(5%)
"""
import logging
import time
from datetime import datetime
from typing import Optional, Tuple

from src.core.context import SharedContext
from src.utils.constants import Direction

logger = logging.getLogger("unified_trader.strategy.trend_following.rules")


class RuleEngine:
    """
    确定性规则决策引擎

    职责：
    1. 接收 AI 方向判断
    2. 融合 MACD、Taker、SmartMoney、RSI 打分
    3. 7 层硬过滤
    4. 输出最终交易方向
    """

    def __init__(self, config: dict):
        self.config = config

        # 权重
        w = config.get("weights", {})
        self.w_ai = w.get("ai", 50)
        self.w_macd = w.get("macd", 25)
        self.w_taker = w.get("taker", 10)
        self.w_sm = w.get("smart_money", 10)
        self.w_rsi = w.get("rsi", 5)

        # 阈值
        self.entry_threshold = config.get("entry_threshold", 60)
        self.reverse_threshold = config.get("reverse_threshold", 65)

        # 过滤器
        self.funding_max = config.get("funding_rate_max", 0.003)
        self.cooldown_minutes = config.get("cooldown_minutes", 15)

        # 状态
        self._last_close_time: float = 0.0
        self._consecutive_losses: int = 0
        self._daily_trades: int = 0

    # ═══════ 主决策 ═══════

    def evaluate(self, ctx: SharedContext, symbol: str,
                 ai_direction: str = "neutral",
                 ai_confidence: float = 50.0,
                 market_indicators: Optional[dict] = None) -> Tuple[Direction, float, dict]:
        """
        综合评估

        Args:
            ai_direction: AI 判断方向 "bullish"|"bearish"|"neutral"
            ai_confidence: AI 置信度 0-100
            market_indicators: MACD, Taker, SmartMoney, RSI 等

        Returns:
            (direction, score, details)
        """
        ind = market_indicators or {}
        details = {}

        # 1. 打分
        ai_score = self._ai_to_score(ai_direction, ai_confidence)
        macd_score = self._macd_to_score(ind.get("macd_signal", 0))
        taker_score = self._taker_to_score(ind.get("taker_ratio", 1.0))
        sm_score = self._sm_to_score(ind.get("sm_alignment", 50))
        rsi_score = self._rsi_to_score(ind.get("rsi", 50))

        score = (
            ai_score * self.w_ai / 100 +
            macd_score * self.w_macd / 100 +
            taker_score * self.w_taker / 100 +
            sm_score * self.w_sm / 100 +
            rsi_score * self.w_rsi / 100
        )

        details = {
            "ai": ai_score,
            "macd": macd_score,
            "taker": taker_score,
            "smart_money": sm_score,
            "rsi": rsi_score,
            "composite": score,
        }

        # 2. 硬过滤
        ok, reason = self._apply_filters(ctx, symbol, ind)
        if not ok:
            details["filter_reason"] = reason
            return Direction.NEUTRAL, score, details

        # 3. 方向判定
        if score >= self.entry_threshold:
            direction = Direction.LONG
        elif score <= (100 - self.entry_threshold):
            direction = Direction.SHORT
        else:
            direction = Direction.NEUTRAL

        return direction, score, details

    # ═══════ 子打分 ═══════

    @staticmethod
    def _ai_to_score(direction: str, confidence: float) -> float:
        """AI → 0-100 (bullish>50, bearish<50)"""
        if direction == "bullish":
            return 50 + confidence / 2
        elif direction == "bearish":
            return 50 - confidence / 2
        return 50.0

    @staticmethod
    def _macd_to_score(signal: float) -> float:
        """MACD → 0-100"""
        if signal > 0:
            return min(100, 50 + signal * 10)
        elif signal < 0:
            return max(0, 50 + signal * 10)
        return 50.0

    @staticmethod
    def _taker_to_score(ratio: float) -> float:
        """Taker ratio → 0-100"""
        if ratio > 1.0:
            return min(100, ratio * 50)
        elif ratio > 0:
            return max(0, ratio * 50)
        return 50.0

    @staticmethod
    def _sm_to_score(alignment: float) -> float:
        """Smart money alignment → 0-100"""
        return max(0, min(100, alignment))

    @staticmethod
    def _rsi_to_score(rsi: float) -> float:
        """RSI → 0-100"""
        if rsi > 70:
            return max(0, 50 - (rsi - 70) * 1.5)
        elif rsi < 30:
            return min(100, 50 + (30 - rsi) * 1.5)
        return 50.0

    # ═══════ 硬过滤 ═══════

    def _apply_filters(self, ctx: SharedContext, symbol: str,
                       ind: dict) -> Tuple[bool, str]:
        """
        7 层硬过滤（按优先级）：
        1. 连续亏损熔断
        2. 时间过滤
        3. 波动率过滤
        4. 主力否决
        5. 资金费率
        6. 冷却期
        7. 日交易上限
        """
        # 1. 连续亏损
        max_losses = self.config.get("max_consecutive_losses", 3)
        if self._consecutive_losses >= max_losses:
            return False, f"连续亏损熔断: {self._consecutive_losses}次"

        # 2. 时间
        hour = datetime.utcnow().hour
        if hour in set(range(8)):  # UTC 0-8
            return False, f"时间过滤: UTC {hour}h"

        # 3. 波动率
        market = ctx.get_market(symbol)
        if market:
            price_5m = market.price_history.get(300, 0)
            current = market.mark_price or 0
            if price_5m > 0 and current > 0:
                change = abs(current - price_5m) / price_5m * 100
                if change > 3.0:
                    return False, f"波动率过大: {change:.1f}%"

        # 4. 主力否决
        sm = ctx.get_smart_money(symbol)
        if sm and sm.top_account_ratio < 1.0 and sm.top_account_ratio > 0:
            # 大户偏空 → 否决 LONG
            return False, f"大户偏空: {sm.top_account_ratio:.2f}"

        # 5. 资金费率
        if market and abs(market.funding_rate) > self.funding_max:
            return False, f"资金费率过高: {market.funding_rate:.4f}"

        # 6. 冷却期
        if self._last_close_time > 0:
            if (time.time() - self._last_close_time) < self.cooldown_minutes * 60:
                return False, "冷却中"

        # 7. 日交易上限
        daily_max = self.config.get("max_trades_per_day", 20)
        if self._daily_trades >= daily_max:
            return False, f"日交易上限: {self._daily_trades}/{daily_max}"

        return True, ""

    # ═══════ 状态更新 ═══════

    def on_trade_closed(self, pnl: float) -> None:
        """记录交易结果"""
        self._last_close_time = time.time()
        self._daily_trades += 1
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def reset_daily(self) -> None:
        self._daily_trades = 0
