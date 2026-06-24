"""
共享过滤器 — 信号生成前/入场前的硬过滤
"""
import logging
import time
from datetime import datetime
from typing import Optional, Tuple

from src.core.context import SharedContext

logger = logging.getLogger("unified_trader.risk.filters")


class SignalFilters:
    """
    信号过滤器集合

    用法:
        filters = SignalFilters(config)
        ok, reason = filters.check_all(ctx, symbol)
        if not ok:
            return NEUTRAL  # 信号被过滤
    """

    def __init__(self, config: dict):
        self.config = config

        # 时间过滤
        self._time_enabled = config.get("time_filter_enabled", True)
        self._skip_hours: set[int] = set(config.get("skip_utc_hours", list(range(8))))

        # 波动率过滤
        self._vol_enabled = config.get("volatility_filter_enabled", True)
        self._max_vol_pct = config.get("max_volatility_pct", 3.0)

        # BTC 闪崩过滤
        self._btc_crash_enabled = config.get("btc_crash_filter_enabled", True)
        self._btc_crash_pct = config.get("btc_crash_threshold_pct", 2.0)

        # OI 背离过滤
        self._oi_div_enabled = config.get("oi_divergence_enabled", True)
        self._oi_div_threshold = config.get("oi_divergence_threshold_pct", 1.0)

        # 主力确认过滤
        self._sm_enabled = config.get("smart_money_filter_enabled", True)
        self._min_top_acct = config.get("min_top_account_ratio", 1.0)
        self._min_taker = config.get("min_taker_buy_sell_ratio", 0.7)

        # 资金费率过滤
        self._funding_max = config.get("funding_rate_max", 0.003)

    def check_all(self, ctx: SharedContext, symbol: str,
                  btc_change_pct: float = 0.0, btc_trend_bias: float = 0.0,
                  oi_delta_pct: float = 0.0) -> Tuple[bool, str]:
        """
        按优先级执行所有过滤器
        Returns: (pass, reason_if_blocked)
        """
        # 1. 时间过滤
        ok, reason = self.check_time()
        if not ok:
            return False, reason

        # 2. BTC 闪崩
        ok, reason = self.check_btc_crash(btc_change_pct, btc_trend_bias)
        if not ok:
            return False, reason

        # 3. 波动率
        ok, reason = self.check_volatility(ctx, symbol)
        if not ok:
            return False, reason

        # 4. OI 背离
        ok, reason = self.check_oi_divergence(ctx, symbol, oi_delta_pct)
        if not ok:
            return False, reason

        # 5. 主力确认
        ok, reason = self.check_smart_money(ctx, symbol)
        if not ok:
            return False, reason

        return True, ""

    # ═══════ 时间过滤 ═══════

    def check_time(self) -> Tuple[bool, str]:
        if not self._time_enabled:
            return True, ""
        hour = datetime.utcnow().hour
        if hour in self._skip_hours:
            return False, f"时间过滤: UTC {hour}h 为跳过时段"
        return True, ""

    # ═══════ BTC 闪崩 ═══════

    def check_btc_crash(self, btc_change_pct: float, btc_trend_bias: float) -> Tuple[bool, str]:
        if not self._btc_crash_enabled:
            return True, ""
        # BTC 5分钟跌超阈值 → 暂停开多
        if btc_change_pct < -self._btc_crash_pct:
            return False, f"BTC 闪崩: {btc_change_pct:.1f}% < {-self._btc_crash_pct}%"
        # BTC 在 EMA50 下方 → 熊市不做多
        if btc_trend_bias < 0:
            return False, f"BTC 趋势偏空: EMA偏差={btc_trend_bias:.1f}"
        return True, ""

    # ═══════ 波动率过滤 ═══════

    def check_volatility(self, ctx: SharedContext, symbol: str) -> Tuple[bool, str]:
        if not self._vol_enabled:
            return True, ""
        market = ctx.get_market(symbol)
        if not market:
            return True, ""
        price_5m = market.price_history.get(300, 0)
        price_now = market.price_history.get(60, 0)
        best_bid = market.orderbook.get("bids", [[0]])[0][0] if market.orderbook and market.orderbook.get("bids") else 0
        current = best_bid or price_now or market.mark_price
        if price_5m > 0 and current > 0:
            change = abs(current - price_5m) / price_5m * 100
            if change > self._max_vol_pct:
                return False, f"波动率过大: {change:.1f}% > {self._max_vol_pct}%"
        return True, ""

    # ═══════ OI 背离 ═══════

    def check_oi_divergence(self, ctx: SharedContext, symbol: str, oi_delta_pct: float) -> Tuple[bool, str]:
        if not self._oi_div_enabled:
            return True, ""
        # OI 下降 + 价格上涨 = 空头平仓假突破
        if oi_delta_pct < -self._oi_div_threshold:
            market = ctx.get_market(symbol)
            if market:
                price_5m = market.price_history.get(300, 0)
                current = market.mark_price
                if price_5m > 0 and current > price_5m:
                    return False, f"OI背离: 价格涨但OI跌 {oi_delta_pct:.1f}%"
        return True, ""

    # ═══════ 主力确认 ═══════

    def check_smart_money(self, ctx: SharedContext, symbol: str) -> Tuple[bool, str]:
        if not self._sm_enabled:
            return True, ""
        sm = ctx.get_smart_money(symbol)
        if not sm:
            return True, ""  # 数据未就绪不阻塞

        # 大户多头账户比例 < 阈值 → 大户不看好
        if sm.top_account_ratio < self._min_top_acct and sm.top_account_ratio > 0:
            return False, f"大户偏空: top_acct={sm.top_account_ratio:.2f} < {self._min_top_acct}"

        # 主动买卖比 < 阈值 → 主动卖出占优
        if sm.taker_buy_sell_ratio < self._min_taker and sm.taker_buy_sell_ratio > 0:
            return False, f"主动卖出占优: taker={sm.taker_buy_sell_ratio:.2f} < {self._min_taker}"

        return True, ""

    # ═══════ 资金费率 ═══════

    def check_funding_rate(self, ctx: SharedContext, symbol: str) -> Tuple[bool, str]:
        """检查资金费率是否过高"""
        market = ctx.get_market(symbol)
        if market and abs(market.funding_rate) > self._funding_max:
            return False, f"资金费率过高: {market.funding_rate:.4f}"
        return True, ""
