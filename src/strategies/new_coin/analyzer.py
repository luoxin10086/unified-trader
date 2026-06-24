"""
新币信号分析器 — 从 fresh-coin-trader 迁移
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from src.core.context import SharedContext, TradeSignal, MarketData, SmartMoneyData
from src.strategies.new_coin.patterns import PatternDetector, OrderflowPattern
from src.utils.constants import Direction

logger = logging.getLogger("unified_trader.strategy.new_coin.analyzer")


@dataclass
class AnalysisResult:
    """分析结果"""
    direction: Direction = Direction.NEUTRAL
    score: float = 50.0
    flow_score: float = 50.0
    pattern_score: float = 50.0
    reason: str = ""
    net_flow: float = 0.0
    large_trade_count: int = 0
    large_buy_vol: float = 0.0
    large_sell_vol: float = 0.0
    buy_ratio: float = 0.5
    window_flow_details: dict = field(default_factory=dict)
    pattern: Optional[OrderflowPattern] = None
    feature_scores: dict[str, float] = field(default_factory=dict)
    triggered_patterns: list[str] = field(default_factory=list)


class NewCoinAnalyzer:
    """
    新币种信号分析器

    两层评分：
    1. 多窗口大单资金流 (50%)
    2. Orderflow 形态检测 (50%)

    决策：composite >= long_threshold → LONG
    """

    def __init__(self, config: dict):
        self.config = config

        # 阈值
        self.long_threshold = config.get("signal_long_threshold", 60)
        self.large_threshold_base = config.get("large_order_threshold_usdt", 50000)
        self.dynamic_threshold = config.get("large_order_threshold_dynamic", True)
        self.money_flow_windows = config.get("money_flow_windows", [1, 5, 15])

        # 检测器
        self.pattern_detector = PatternDetector()

        # 冷却
        self._cooldowns: dict[str, float] = {}
        self._cooldown_minutes = config.get("signal_cooldown_minutes", 5)

    # ═══════ 主入口 ═══════

    def analyze(self, symbol: str, ctx: SharedContext,
                btc_change_pct: float = 0.0,
                btc_trend_bias: float = 0.0,
                oi_delta_pct: float = 0.0) -> AnalysisResult:
        """
        生成交易信号

        Returns:
            AnalysisResult with direction, score, feature data
        """
        result = AnalysisResult()

        market = ctx.get_market(symbol)
        if not market:
            result.reason = "无市场数据"
            return result

        trades = market.trades
        if not trades:
            result.reason = "无交易数据"
            return result

        # 冷却检查
        if symbol in self._cooldowns:
            if time.time() - self._cooldowns[symbol] < self._cooldown_minutes * 60:
                result.reason = "冷却中"
                return result

        # 1. 计算动态大单阈值
        threshold = self._calc_threshold(trades)

        # 2. 多窗口资金流分析
        flow_scores, window_details = self._analyze_multi_window(trades, threshold)

        # 检查方向一致性
        directions = [self._score_to_direction(s) for s in flow_scores]
        all_long = all(d == Direction.LONG for d in directions)
        all_short = all(d == Direction.SHORT for d in directions)

        if not all_long and not all_short:
            flow_score = 50.0  # 窗口不一致 → 中性
        else:
            flow_score = sum(flow_scores) / len(flow_scores)

        # 3. 形态检测
        pattern = self.pattern_detector.detect(trades, threshold)
        pattern_score = self._calc_pattern_score(pattern)

        # 4. 融合评分
        composite = flow_score * 0.5 + pattern_score * 0.5

        # 5. 方向判定
        if composite >= self.long_threshold:
            direction = Direction.LONG
        elif composite <= (100 - self.long_threshold):
            direction = Direction.SHORT
        else:
            direction = Direction.NEUTRAL

        # 6. 统计
        large_trades = [t for t in trades if t["value"] >= threshold]
        large_buy = sum(t["value"] for t in large_trades if t["side"] == "BUY")
        large_sell = sum(t["value"] for t in large_trades if t["side"] == "SELL")
        net_flow = large_buy - large_sell
        total_large = large_buy + large_sell
        buy_ratio = large_buy / total_large if total_large > 0 else 0.5

        # 7. 特征评分（记录用）
        feature_scores = self._compute_feature_scores(market, ctx.get_smart_money(symbol))

        triggered = []
        if pattern.absorption:
            triggered.append("ABSORPTION")
        if pattern.imbalance:
            triggered.append(f"IMBALANCE_{pattern.imbalance_direction}")
        if pattern.exhaustion:
            triggered.append("EXHAUSTION")
        if pattern.divergence:
            triggered.append("DIVERGENCE")
        if pattern.large_support_small_pressure:
            triggered.append("LARGE_SUPPORT")
        if pattern.same_price_repeat:
            triggered.append("SAME_PRICE")

        return AnalysisResult(
            direction=direction,
            score=composite,
            flow_score=flow_score,
            pattern_score=pattern_score,
            reason=self._build_reason(direction, composite, flow_score, pattern_score, triggered),
            net_flow=net_flow,
            large_trade_count=len(large_trades),
            large_buy_vol=large_buy,
            large_sell_vol=large_sell,
            buy_ratio=buy_ratio,
            window_flow_details=window_details,
            pattern=pattern,
            feature_scores=feature_scores,
            triggered_patterns=triggered,
        )

    def reset_cooldown(self, symbol: str) -> None:
        """平仓后重置冷却"""
        self._cooldowns.pop(symbol, None)

    # ═══════ 动态阈值 ═══════

    def _calc_threshold(self, trades: list) -> float:
        """动态大单阈值：P95 × 3，上限 base 下限 2000"""
        if not self.dynamic_threshold:
            return self.large_threshold_base

        values = sorted([t["value"] for t in trades[-100:]])
        if not values:
            return self.large_threshold_base

        p95_index = int(len(values) * 0.95)
        p95 = values[p95_index] if p95_index < len(values) else values[-1]

        return max(2000, min(p95 * 3, self.large_threshold_base))

    # ═══════ 多窗口资金流 ═══════

    def _analyze_multi_window(self, trades: list, threshold: float) -> tuple:
        """多窗口大单资金流分析"""
        now = time.time()
        scores = []
        details = {}

        for window_min in self.money_flow_windows:
            cutoff = now - window_min * 60
            window_trades = [t for t in trades if t["ts"] >= cutoff]
            large = [t for t in window_trades if t["value"] >= threshold]

            buys = sum(t["value"] for t in large if t["side"] == "BUY")
            sells = sum(t["value"] for t in large if t["side"] == "SELL")
            total = buys + sells

            if total > 0:
                score = buys / total * 100
            else:
                score = 50.0

            scores.append(score)
            details[f"{window_min}m"] = {
                "score": score,
                "buys": buys,
                "sells": sells,
                "total": total,
                "large_count": len(large),
            }

        return scores, details

    @staticmethod
    def _score_to_direction(score: float) -> Direction:
        if score > 60:
            return Direction.LONG
        elif score < 40:
            return Direction.SHORT
        return Direction.NEUTRAL

    # ═══════ 形态评分 ═══════

    def _calc_pattern_score(self, pattern: OrderflowPattern) -> float:
        """计算形态综合评分"""
        score = 50.0

        if pattern.absorption:
            score += pattern.absorption_score * 0.3

        if pattern.imbalance:
            if pattern.imbalance_direction == "LONG":
                score += min(20, (pattern.imbalance_ratio - 1) * 5)
            else:
                score -= min(20, (pattern.imbalance_ratio - 1) * 5)

        if pattern.large_support_small_pressure:
            score += 10.0

        # 限制范围
        return max(0, min(100, score))

    # ═══════ 特征评分（记录用，不参与决策） ═══════

    def _compute_feature_scores(self, market: MarketData,
                                sm: Optional[SmartMoneyData]) -> dict:
        """计算所有辅助特征的 0-100 分数"""
        scores = {}

        # 大户数据
        if sm:
            scores["top_account_ratio"] = self._ratio_to_score(sm.top_account_ratio)
            scores["top_position_ratio"] = self._ratio_to_score(sm.top_position_ratio)
            scores["global_account_ratio"] = self._ratio_to_score(sm.global_account_ratio)
            scores["taker_buy_sell_ratio"] = self._ratio_to_score(sm.taker_buy_sell_ratio)

        # 订单簿
        scores["ob_imbalance_5"] = self._ratio_to_score(market.ob_imbalance_5 * 2 + 1)
        scores["ob_imbalance_20"] = self._ratio_to_score(market.ob_imbalance_20 * 2 + 1)

        # 成交量激增
        scores["volume_surge"] = self._ratio_to_score(market.volume_surge_ratio)

        # 主动买卖比
        if market.taker_sell_volume_5min > 0:
            taker_ratio = market.taker_buy_volume_5min / market.taker_sell_volume_5min
            scores["taker_buy_sell_5min"] = self._ratio_to_score(taker_ratio)

        # 大流入
        scores["large_inflow_duration"] = self._ratio_to_score(
            market.large_inflow_duration / 60.0 * 10
        ) if market.large_inflow_duration > 0 else 50.0
        scores["large_inflow_accumulated"] = self._ratio_to_score(
            market.large_inflow_accumulated / 50000
        ) if market.large_inflow_accumulated > 0 else 50.0

        return scores

    @staticmethod
    def _ratio_to_score(ratio: float) -> float:
        """比率 → 0-100 (1.0=50)"""
        if ratio <= 0:
            return 50.0
        return max(0, min(100, ratio * 50))

    @staticmethod
    def _build_reason(direction: Direction, score: float,
                      flow: float, pattern: float,
                      triggered: list) -> str:
        if direction == Direction.NEUTRAL:
            return f"NEUTRAL: flow={flow:.0f} pattern={pattern:.0f}"
        parts = [
            f"{direction.value} score={score:.1f}",
            f"flow={flow:.0f} pattern={pattern:.0f}",
        ]
        if triggered:
            parts.append("+".join(triggered[:3]))
        return " | ".join(parts)
