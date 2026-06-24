"""
数据源管理器 — 编排 WebSocket + REST + Collector
"""
import logging
import threading
import time
from typing import Optional

from src.core.context import SharedContext
from src.data.rest_client import BinanceRESTClient
from src.data.ws_client import BinanceWSClient, SymbolFeed
from src.data.collector import DataCollector

logger = logging.getLogger("unified_trader.data.feed")


class FeedManager:
    """
    统一数据源管理器

    职责：
    1. 管理 WS 客户端和 REST 客户端
    2. 根据策略需求聚合 symbol 列表，去重后订阅
    3. 每 tick 将 SymbolFeed 同步到 SharedContext
    4. 管理 DataCollector 定时采集
    """

    def __init__(self, ctx: SharedContext, config: dict):
        self.ctx = ctx
        self.config = config

        # REST 客户端
        self.rest = BinanceRESTClient(config)

        # WS 客户端（每收到 aggTrade 触发 _on_tick）
        self.ws = BinanceWSClient(on_tick=self._on_tick)
        self.ws.set_rest_fallback(self.rest)

        # 采集器
        self.collector = DataCollector(ctx, self.rest)

        # 策略提供的 symbol 映射 {strategy_name: [symbols]}
        self._strategy_symbols: dict[str, list[str]] = {}

        # REST 轮询线程（补充 WS 断线时的数据）
        self._rest_poll_running = False
        self._rest_poll_thread: Optional[threading.Thread] = None

        logger.info("FeedManager 初始化完成")

    # ═══════ Symbol 管理 ═══════

    def register_strategy(self, strategy_name: str, symbols: list[str]) -> None:
        """策略注册自己关注的币种"""
        self._strategy_symbols[strategy_name] = list(symbols)
        self._update_subscriptions()

    def update_strategy_symbols(self, strategy_name: str, symbols: list[str]) -> None:
        """策略更新币种列表"""
        self._strategy_symbols[strategy_name] = list(symbols)
        self._update_subscriptions()

    def get_aggregated_symbols(self) -> list[str]:
        """聚合所有策略的 symbol 列表（去重）"""
        all_syms = set()
        for syms in self._strategy_symbols.values():
            all_syms.update(syms)
        return sorted(all_syms)

    def _update_subscriptions(self) -> None:
        """更新 WS 订阅"""
        symbols = self.get_aggregated_symbols()
        if not symbols:
            return
        self.ws.update_symbols(symbols)

    # ═══════ 启动/停止 ═══════

    def start(self) -> None:
        """启动数据采集"""
        symbols = self.get_aggregated_symbols()
        if not symbols:
            logger.warning("没有币种需要采集，等待策略注册...")
        else:
            self.ws.subscribe(symbols)
            self.ws.start()

        self.collector.start()
        self._start_rest_poll()
        logger.info("数据源管理器已启动: %d 币种", len(symbols))

    def stop(self) -> None:
        """停止数据采集"""
        self.ws.stop()
        self.collector.stop()
        self._rest_poll_running = False
        if self._rest_poll_thread:
            self._rest_poll_thread.join(timeout=10)
        logger.info("数据源管理器已停止")

    def is_ws_alive(self) -> bool:
        """检查 WebSocket 是否存活"""
        return self.ws._running

    # ═══════ 数据同步 ═══════

    def _on_tick(self, symbol: str) -> None:
        """
        每次收到 aggTrade 时调用
        将 SymbolFeed 同步到 SharedContext
        """
        feed = self.ws.get_feed(symbol)
        if feed:
            market_data = feed.to_market_data()
            self.ctx.update_market(market_data)

    def get_feed(self, symbol: str) -> Optional[SymbolFeed]:
        """获取单个币种的 feed"""
        return self.ws.get_feed(symbol)

    # ═══════ REST 轮询（WS 备份） ═══════

    def _start_rest_poll(self) -> None:
        """启动 REST 轮询线程 — WS 数据不足时的补充"""
        self._rest_poll_running = True
        self._rest_poll_thread = threading.Thread(target=self._rest_poll_loop, daemon=True)
        self._rest_poll_thread.start()

    def _rest_poll_loop(self) -> None:
        """REST 轮询 — 每 10 秒补充一次"""
        while self._rest_poll_running:
            try:
                symbols = self.get_aggregated_symbols()
                for sym in symbols:
                    feed = self.ws.get_feed(sym)
                    if not feed:
                        continue

                    # 如果 WS 订单簿超过 30 秒没更新，用 REST 补
                    ob_age = time.time() - feed.orderbook.get("last_update", 0) / 1000.0
                    if ob_age > 30:
                        ob = self.rest.get_orderbook(sym, limit=20)
                        if ob:
                            feed.orderbook = {
                                **ob,
                                "last_update": int(time.time() * 1000),
                            }

                    # 价格为空时补
                    if feed.mark_price == 0:
                        price = self.rest.get_price(sym)
                        if price > 0:
                            feed.mark_price = price

            except Exception:
                pass

            time.sleep(10)
