"""
新币种扫描器 — 从 fresh-coin-trader 迁移
"""
import logging
import re
import time
from collections import deque
from typing import Optional

from src.data.rest_client import BinanceRESTClient

logger = logging.getLogger("unified_trader.strategy.new_coin.scanner")

# 黑名单：非交易对
SYMBOL_BLACKLIST = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "DOTUSDT",
    "MATICUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT",
}
SYMBOL_BLACKLIST |= {s for s in SYMBOL_BLACKLIST}

# 杠杆代币后缀
LEVERAGED_TOKEN_PATTERN = re.compile(r"(UP|DOWN|BULL|BEAR)\b", re.I)


class NewCoinScanner:
    """
    新币种发现

    1. 从交易所获取所有 USDT-M 合约
    2. 按上线时间排序，取最新的 N 个
    3. 过滤：上线时间 >= 24h、成交量 >= 1M USDT、涨幅 < 50%
    4. 成交量激增检测（补充发现）
    """

    def __init__(self, rest: BinanceRESTClient, config: dict):
        self.rest = rest
        self.config = config
        self.top_n = config.get("scan_top_n", 10)
        self.max_price_rise = config.get("max_price_rise_pct", 50.0)
        self.min_onboard_hours = config.get("min_onboard_hours", 24)
        self.min_24h_vol = config.get("min_24h_volume_usdt", 1000000)

        # 成交量激增
        self.vol_surge_enabled = config.get("volume_surge_enabled", True)
        self.vol_surge_multiplier = config.get("volume_surge_multiplier", 5.0)
        self._vol_history: dict[str, deque] = {}

    def get_new_symbols(self) -> list[str]:
        """获取最新上线的 N 个币种"""
        try:
            info = self.rest.get_exchange_info()
            symbols = info.get("symbols", [])
        except Exception as e:
            logger.warning("获取交易对列表失败: %s", e)
            return []

        candidates = []

        for s in symbols:
            sym = s.get("symbol", "")
            if not _is_valid_symbol(sym):
                continue
            if sym in SYMBOL_BLACKLIST:
                continue

            onboard = s.get("onboardDate", 0)
            candidates.append((sym, onboard))

        # 按上线时间降序，取最新的
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in candidates[:self.top_n]]

    def filter_by_price_rise(self, symbols: list[str]) -> list[str]:
        """过滤涨幅过大或成交量过小的币种"""
        valid = []

        for sym in symbols:
            try:
                ticker = self.rest.get_24h_ticker(sym)
                if not ticker:
                    continue
                t = ticker[0] if isinstance(ticker, list) else ticker

                price_change = abs(float(t.get("priceChangePercent", 0)))
                volume = float(t.get("quoteVolume", 0))
                last_price = float(t.get("lastPrice", 0))

                # 过滤
                if last_price < 0.01 or last_price > 1000:
                    continue
                if price_change > self.max_price_rise:
                    continue
                if volume < self.min_24h_vol:
                    continue

                # 上线时间检查
                info = self.rest.get_symbol_info(sym)
                onboard = info.get("onboard_date", 0)
                if onboard > 0:
                    hours_since = (time.time() * 1000 - onboard) / 3600000
                    if hours_since < self.min_onboard_hours:
                        continue

                valid.append(sym)

            except Exception:
                continue

        return valid

    def get_volume_surge_symbols(self) -> list[str]:
        """获取成交量激增的币种"""
        if not self.vol_surge_enabled:
            return []

        surge_symbols = []
        for sym, history in self._vol_history.items():
            if len(history) < 3:
                continue
            avg = sum(history) / len(history)
            current = history[-1]
            if avg > 0 and current / avg >= self.vol_surge_multiplier:
                surge_symbols.append(sym)

        return surge_symbols

    def poll_24h_volumes(self, symbols: list[str]) -> None:
        """采集24h成交量用于计算激增"""
        for sym in symbols:
            try:
                ticker = self.rest.get_24h_ticker(sym)
                if ticker:
                    t = ticker[0] if isinstance(ticker, list) else ticker
                    vol = float(t.get("quoteVolume", 0))
                    if sym not in self._vol_history:
                        self._vol_history[sym] = deque(maxlen=10)
                    self._vol_history[sym].append(vol)
            except Exception:
                pass


def _is_valid_symbol(symbol: str) -> bool:
    """验证币种是否合法"""
    if not symbol:
        return False
    if not symbol.endswith("USDT"):
        return False
    if LEVERAGED_TOKEN_PATTERN.search(symbol):
        return False
    if symbol in SYMBOL_BLACKLIST:
        return False
    if not symbol.isascii():
        return False
    # 只含字母
    base = symbol[:-4]
    if not base.isalpha():
        return False
    return True
