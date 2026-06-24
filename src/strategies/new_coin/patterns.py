"""
Orderflow 形态检测 — 从 fresh-coin-trader 迁移
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("unified_trader.strategy.new_coin.patterns")


@dataclass
class OrderflowPattern:
    """形态检测结果"""
    absorption: bool = False
    absorption_score: float = 0.0

    imbalance: bool = False
    imbalance_direction: str = ""
    imbalance_ratio: float = 0.0

    exhaustion: bool = False
    exhaustion_score: float = 0.0

    divergence: bool = False
    divergence_score: float = 0.0

    large_support_small_pressure: bool = False

    same_price_repeat: bool = False
    same_price_repeat_score: float = 0.0

    price_position: float = 0.0  # 0=low, 1=high in window range


class PatternDetector:
    """Wyckoff 启发式形态检测器"""

    def detect(self, trades: list[dict], large_threshold: float) -> OrderflowPattern:
        """
        检测所有形态

        Args:
            trades: 最近一段时间的成交列表 [{side, price, qty, value}, ...]
            large_threshold: 大单阈值 (USDT)
        """
        pattern = OrderflowPattern()

        if not trades:
            return pattern

        large_trades = [t for t in trades if t["value"] >= large_threshold]
        if not large_trades:
            return pattern

        prices = [t["price"] for t in trades]
        price_range = (max(prices) - min(prices)) / prices[-1] * 100 if prices else 0

        # 价格位置
        if price_range > 0:
            pattern.price_position = (prices[-1] - min(prices)) / (max(prices) - min(prices))
        else:
            pattern.price_position = 0.5

        # 1. ABSORPTION — 大成交量 + 价格稳定 → 主力吸筹
        pattern.absorption = self._detect_absorption(large_trades, price_range, trades)

        # 2. IMBALANCE — 买卖严重失衡
        pattern.imbalance, pattern.imbalance_direction, pattern.imbalance_ratio = \
            self._detect_imbalance(large_trades)

        # 3. EXHAUSTION — 大单数量衰减
        pattern.exhaustion = self._detect_exhaustion(large_trades)

        # 4. DIVERGENCE — 成交量放大但价格不动
        pattern.divergence = self._detect_divergence(trades, price_range)

        # 5. LARGE_SUPPORT — 大单买+小单卖
        pattern.large_support_small_pressure = self._detect_large_support(trades, large_threshold, price_range)

        # 6. SAME_PRICE — 同价反复成交
        pattern.same_price_repeat = self._detect_same_price(trades, price_range)

        # 评分
        if pattern.absorption:
            pattern.absorption_score = min(30, max(5, len(large_trades) * 3))

        if pattern.imbalance and pattern.imbalance_direction == "LONG":
            pattern.imbalance_ratio = min(5.0, pattern.imbalance_ratio)

        if pattern.exhaustion:
            pattern.exhaustion_score = 10.0

        if pattern.divergence:
            pattern.divergence_score = 15.0

        if pattern.same_price_repeat:
            pattern.same_price_repeat_score = 5.0

        return pattern

    # ═══════ 检测方法 ═══════

    def _detect_absorption(self, large_trades: list, price_range: float,
                           all_trades: list) -> bool:
        """ABSORPTION: >=2 大单 + 价格波动 < 0.3%"""
        if len(large_trades) < 2:
            return False
        if price_range > 0.3:
            return False
        total_volume = sum(t["value"] for t in large_trades)
        # 大单总金额需要超过阈值
        if total_volume < 100000:
            return False
        return True

    def _detect_imbalance(self, large_trades: list) -> tuple:
        """IMBALANCE: 买卖比 >= 3:1"""
        buys = sum(t["value"] for t in large_trades if t["side"] == "BUY")
        sells = sum(t["value"] for t in large_trades if t["side"] == "SELL")

        if sells > 0 and buys / sells >= 3:
            return True, "LONG", buys / sells
        if buys > 0 and sells / buys >= 3:
            return True, "SHORT", sells / buys
        if sells == 0 and buys > 0:
            return True, "LONG", 5.0
        if buys == 0 and sells > 0:
            return True, "SHORT", 5.0
        return False, "", 1.0

    def _detect_exhaustion(self, large_trades: list) -> bool:
        """EXHAUSTION: 后半段大单数量 < 前半段的 50%"""
        half = len(large_trades) // 2
        if half < 2:
            return False
        first = large_trades[:half]
        second = large_trades[half:]
        if len(first) > 0 and len(second) / len(first) < 0.5:
            return True
        return False

    def _detect_divergence(self, trades: list, price_range: float) -> bool:
        """DIVERGENCE: 后半段成交量 >= 前半段 2x + 价格变化 < 0.5%"""
        if len(trades) < 10:
            return False
        half = len(trades) // 2
        first_vol = sum(t["value"] for t in trades[:half])
        second_vol = sum(t["value"] for t in trades[half:])
        if first_vol > 0 and second_vol / first_vol >= 2 and price_range < 0.5:
            return True
        return False

    def _detect_large_support(self, trades: list, threshold: float,
                              price_range: float) -> bool:
        """LARGE_SUPPORT: 大单 >=70% 买 + 小单 >=60% 卖 + 价格稳定"""
        large = [t for t in trades if t["value"] >= threshold]
        small = [t for t in trades if t["value"] < threshold * 0.5]

        if not large or not small:
            return False

        large_buy_pct = sum(1 for t in large if t["side"] == "BUY") / len(large) * 100
        small_sell_pct = sum(1 for t in small if t["side"] == "SELL") / len(small) * 100

        return large_buy_pct >= 70 and small_sell_pct >= 60 and price_range <= 0.5

    def _detect_same_price(self, trades: list, price_range: float) -> bool:
        """SAME_PRICE: >=30% 成交同价 + 价格范围 < 0.5%"""
        if len(trades) < 20:
            return False
        # 统计相同价格
        price_counts = {}
        for t in trades:
            p = round(t["price"], 1)  # 四舍五入到0.1
            price_counts[p] = price_counts.get(p, 0) + 1

        max_count = max(price_counts.values())
        return max_count / len(trades) >= 0.3 and price_range < 0.5
