"""
数据采集器 — 定时拉取 REST 数据
"""
import logging
import threading
import time
from typing import Optional

from src.core.context import SharedContext, SmartMoneyData
from src.data.rest_client import BinanceRESTClient

logger = logging.getLogger("unified_trader.data.collector")


class DataCollector:
    """
    定时采集 REST API 数据

    - 大户数据（每5分钟）
    - OI 快照（每5分钟，用于 OI 增量计算）
    - 资金费率（每5分钟）
    - 24h行情（每5分钟）
    """

    def __init__(self, ctx: SharedContext, rest: BinanceRESTClient):
        self.ctx = ctx
        self.rest = rest
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._interval = 300  # 5分钟

        # OI 快照 {symbol: last_oi}
        self._oi_snapshots: dict[str, float] = {}

    def start(self) -> None:
        """启动采集线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("数据采集器已启动 (每 %ds)", self._interval)

    def stop(self) -> None:
        """停止采集"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def collect_once(self) -> None:
        """执行一次完整采集"""
        symbols = self.ctx.get_all_symbols()
        if not symbols:
            return

        logger.debug("采集 %d 个币种的数据...", len(symbols))

        for i, sym in enumerate(symbols):
            try:
                # 大户数据
                sm = self.rest.get_smart_money(sym)
                if sm:
                    self.ctx.update_smart_money(SmartMoneyData(
                        symbol=sym,
                        **sm,
                    ))

                # OI 变化
                oi = self.rest.get_open_interest(sym)
                if oi > 0:
                    prev = self._oi_snapshots.get(sym, 0)
                    oi_delta = oi - prev if prev > 0 else 0
                    oi_delta_pct = oi_delta / prev * 100 if prev > 0 else 0
                    self._oi_snapshots[sym] = oi

                # 错开请求，避免 API 权重骤增
                time.sleep(0.5)

            except Exception as e:
                logger.debug("采集 %s 异常: %s", sym, e)

    def get_oi_delta(self, symbol: str) -> dict:
        """获取 OI 变化"""
        oi = self._oi_snapshots.get(symbol, 0)
        return {"oi": oi}

    def _loop(self) -> None:
        """采集主循环"""
        while self._running:
            try:
                self.collect_once()
            except Exception:
                logger.error("采集循环异常", exc_info=True)
            # 等待下一个周期
            for _ in range(self._interval):
                if not self._running:
                    break
                time.sleep(1)
