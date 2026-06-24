"""
Binance WebSocket 客户端 — 多流复用
"""
import json
import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

from websocket import WebSocketApp

from src.core.context import MarketData

logger = logging.getLogger("unified_trader.data.ws")


class SymbolFeed:
    """
    单个币种的数据缓存 — 接收 WebSocket 推送，计算实时指标

    对应旧系统 SymbolFeed，但接口标准化为 MarketData
    """

    def __init__(self, symbol: str, large_threshold_usdt: float = 50000):
        self.symbol = symbol

        # 成交数据
        self.trades: deque[dict] = deque(maxlen=2000)

        # 订单簿
        self.orderbook: dict = {"bids": [], "asks": [], "last_update": 0}

        # 市场
        self.mark_price: float = 0.0
        self.funding_rate: float = 0.0
        self.open_interest: float = 0.0

        # 爆仓
        self.liquidations: deque[dict] = deque(maxlen=50)

        # 价格历史 (用于 get_price_at)
        self.price_history: deque[tuple[float, float]] = deque(maxlen=600)  # (ts, price)

        # 成交量历史 (用于 volume surge)
        self.volume_history: deque[tuple[float, float]] = deque(maxlen=60)  # (ts, volume)

        # 大单阈值
        self.large_threshold = large_threshold_usdt

        # 大单统计（5分钟窗口）
        self._large_window_5min: deque[dict] = deque(maxlen=500)
        self._taker_buy_vol_5min: float = 0.0
        self._taker_sell_vol_5min: float = 0.0

        # 大流入跟踪
        self._large_inflow_start: float = 0.0
        self._large_inflow_total: float = 0.0
        self._large_inflow_ongoing: bool = False

        # 线程安全
        self._lock = threading.RLock()

    # ═══════ WebSocket 数据更新 ═══════

    def on_agg_trade(self, data: dict) -> None:
        """处理 aggTrade 事件"""
        price = float(data["p"])
        qty = float(data["q"])
        side = "BUY" if data.get("m", False) is False else "SELL"
        # m=False → taker bought from maker's ask → BUY
        value = price * qty
        ts = data.get("T", time.time() * 1000) / 1000.0

        trade = {
            "ts": ts,
            "price": price,
            "qty": qty,
            "value": value,
            "side": side,
            "id": data.get("a", 0),
        }

        with self._lock:
            self.trades.append(trade)
            self.price_history.append((ts, price))
            self._update_large_stats(trade)

    def on_depth(self, data: dict) -> None:
        """处理 depth20@100ms 更新"""
        with self._lock:
            self.orderbook = {
                "bids": [[float(p), float(q)] for p, q in data.get("b", [])],
                "asks": [[float(p), float(q)] for p, q in data.get("a", [])],
                "last_update": data.get("T", 0),
            }

    def on_mark_price(self, data: dict) -> None:
        """处理 markPrice 更新"""
        with self._lock:
            self.mark_price = float(data.get("p", 0))
            self.funding_rate = float(data.get("r", 0))

    def on_open_interest(self, data: dict) -> None:
        """处理 openInterest 更新"""
        with self._lock:
            self.open_interest = float(data.get("oi", 0))

    def on_force_order(self, data: dict) -> None:
        """处理 forceOrder（爆仓）事件"""
        with self._lock:
            self.liquidations.append({
                "ts": data.get("T", time.time() * 1000) / 1000.0,
                "side": data.get("S", ""),
                "price": float(data.get("p", 0)),
                "qty": float(data.get("q", 0)),
                "type": data.get("o", ""),
            })

    # ═══════ 实时计算 ═══════

    def _update_large_stats(self, trade: dict) -> None:
        """更新大单统计"""
        value = trade["value"]
        # 清理超过5分钟的旧数据
        cutoff = trade["ts"] - 300
        while self._large_window_5min and self._large_window_5min[0]["ts"] < cutoff:
            old = self._large_window_5min.popleft()
            if old["side"] == "BUY":
                self._taker_buy_vol_5min -= old["value"]
            else:
                self._taker_sell_vol_5min -= old["value"]

        self._large_window_5min.append(trade)
        if trade["side"] == "BUY":
            self._taker_buy_vol_5min += value
        else:
            self._taker_sell_vol_5min += value

        # 大流入检测
        if value >= self.large_threshold:
            if trade["side"] == "BUY":
                if not self._large_inflow_ongoing:
                    self._large_inflow_start = trade["ts"]
                    self._large_inflow_total = 0
                    self._large_inflow_ongoing = True
                self._large_inflow_total += value
            else:
                self._large_inflow_ongoing = False

    # ═══════ 数据提取 ═══════

    def get_price_at(self, seconds_ago: float) -> float:
        """获取 N 秒前的价格"""
        with self._lock:
            target = time.time() - seconds_ago
            best = 0.0
            for ts, price in self.price_history:
                if ts <= target:
                    best = price
                else:
                    break
            return best

    def get_orderbook_imbalance(self, levels: int = 20) -> float:
        """计算订单簿买卖失衡度 [-1, 1]，正值=买压"""
        with self._lock:
            bids = self.orderbook.get("bids", [])[:levels]
            asks = self.orderbook.get("asks", [])[:levels]
            bid_total = sum(q for _, q in bids)
            ask_total = sum(q for _, q in asks)
            total = bid_total + ask_total
            if total == 0:
                return 0.0
            return (bid_total - ask_total) / total

    def get_volume_surge_ratio(self) -> float:
        """当前成交量 / 5分钟均值"""
        with self._lock:
            now = time.time()
            recent = sum(
                vol for ts, vol in self.volume_history if now - ts <= 60
            )
            avg = sum(
                vol for ts, vol in self.volume_history if now - ts <= 300
            )
            avg_1min = avg / 5 if avg > 0 else recent
            return recent / avg_1min if avg_1min > 0 else 1.0

    def get_taker_buy_sell_ratio(self) -> float:
        """最近5分钟主动买卖比"""
        with self._lock:
            if self._taker_sell_vol_5min > 0:
                return self._taker_buy_vol_5min / self._taker_sell_vol_5min
            return 1.0 if self._taker_buy_vol_5min > 0 else 0.0

    def get_large_inflow_stats(self) -> dict:
        """获取大流入统计"""
        with self._lock:
            duration = 0.0
            if self._large_inflow_ongoing and self._large_inflow_start > 0:
                duration = time.time() - self._large_inflow_start
            return {
                "duration_seconds": duration,
                "accumulated_usdt": self._large_inflow_total,
                "ongoing": self._large_inflow_ongoing,
            }

    def get_large_orders(self, top_n: int = 5) -> dict:
        """检测订单簿上的大单墙"""
        with self._lock:
            bids = self.orderbook.get("bids", [])
            asks = self.orderbook.get("asks", [])
            bid_threshold = bids[0][1] * 5 if bids else 0
            ask_threshold = asks[0][1] * 5 if asks else 0

            large_bids = [
                {"price": p, "qty": q} for p, q in bids[:20]
                if q >= bid_threshold
            ][:top_n]
            large_asks = [
                {"price": p, "qty": q} for p, q in asks[:20]
                if q >= ask_threshold
            ][:top_n]
            return {"bids": large_bids, "asks": large_asks}

    # ═══════ 导出 ═══════

    def to_market_data(self) -> MarketData:
        """导出为统一的 MarketData"""
        with self._lock:
            return MarketData(
                symbol=self.symbol,
                timestamp=time.time(),
                trades=list(self.trades)[-100:],
                orderbook={
                    "bids": [list(b) for b in self.orderbook.get("bids", [])],
                    "asks": [list(a) for a in self.orderbook.get("asks", [])],
                },
                mark_price=self.mark_price,
                open_interest=self.open_interest,
                funding_rate=self.funding_rate,
                liquidations=list(self.liquidations),
                ob_imbalance_5=self.get_orderbook_imbalance(5),
                ob_imbalance_20=self.get_orderbook_imbalance(20),
                volume_surge_ratio=self.get_volume_surge_ratio(),
                taker_buy_volume_5min=self._taker_buy_vol_5min,
                taker_sell_volume_5min=self._taker_sell_vol_5min,
                large_inflow_duration=self._large_inflow_total,
                large_inflow_accumulated=self._large_inflow_total,
                price_history={
                    60: self.get_price_at(60),
                    300: self.get_price_at(300),
                },
                large_orders=self.get_large_orders(5).get("bids", [])
                + self.get_large_orders(5).get("asks", []),
            )


class BinanceWSClient:
    """
    多币种 WebSocket 客户端 — 单连接，多 stream

    用法:
        ws = BinanceWSClient(on_tick=callback)
        ws.subscribe(["BTCUSDT", "ETHUSDT"])
        ws.start()
        ...
        ws.stop()
    """

    STREAM_TYPES = ["aggTrade", "depth20@100ms", "markPrice@1s", "openInterest@1s", "forceOrder"]

    def __init__(self, on_tick: Optional[Callable[[str], None]] = None):
        self._feeds: dict[str, SymbolFeed] = {}
        self._ws: Optional[WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._symbols: list[str] = []
        self._rest_fallback = None  # BinanceRESTClient, injected later
        self._on_tick = on_tick  # 每收到一次 aggTrade 触发
        self._lock = threading.RLock()

    def set_rest_fallback(self, rest_client) -> None:
        """注入 REST 回退客户端"""
        self._rest_fallback = rest_client

    def subscribe(self, symbols: list[str]) -> None:
        """设置订阅币种"""
        with self._lock:
            for sym in symbols:
                if sym not in self._feeds:
                    self._feeds[sym] = SymbolFeed(sym)
            self._symbols = sorted(self._feeds.keys())

    def update_symbols(self, symbols: list[str]) -> None:
        """动态更新币种列表（需要重建连接）"""
        changed = set(symbols) != set(self._symbols)
        if not changed:
            return
        with self._lock:
            for sym in symbols:
                if sym not in self._feeds:
                    self._feeds[sym] = SymbolFeed(sym)
            old_syms = set(self._feeds.keys())
            new_syms = set(symbols)
            for sym in old_syms - new_syms:
                self._feeds.pop(sym, None)
            self._symbols = sorted(symbols)

        if self._running:
            logger.info("币种列表变更，重建 WS 连接...")
            self.stop()
            time.sleep(1)
            self.start()

    def get_feed(self, symbol: str) -> Optional[SymbolFeed]:
        return self._feeds.get(symbol)

    def get_all_symbols(self) -> list[str]:
        return list(self._symbols)

    def start(self) -> None:
        """启动 WebSocket"""
        if self._running:
            return
        if not self._symbols:
            logger.warning("没有订阅币种，WS 未启动")
            return

        self._running = True
        streams = []
        for sym in self._symbols:
            for st in self.STREAM_TYPES:
                streams.append(f"{sym.lower()}@{st}")

        base_url = "wss://fstream.binance.com/stream"
        if self._rest_fallback and self._rest_fallback.testnet:
            base_url = "wss://stream.binancefuture.com/stream"

        # 分批连接（币安限制每连接 200 stream）
        batch_size = 200
        url = f"{base_url}?streams={'/'.join(streams[:batch_size])}"
        logger.info("WS 连接 %d 币种 %d streams: %s", len(self._symbols), len(streams), url[:100])

        self._ws = WebSocketApp(
            url,
            on_message=self._on_message,
            on_open=self._on_open,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止 WebSocket"""
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    # ═══════ WebSocket 回调 ═══════

    def _on_open(self, ws) -> None:
        logger.info("WS 连接成功: %d 币种", len(self._symbols))

    def _on_error(self, ws, error) -> None:
        logger.warning("WS 连接错误: %s", error)

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        logger.warning("WS 断开: code=%s msg=%s", close_status_code, close_msg)
        if self._running:
            logger.info("5秒后自动重连...")
            time.sleep(5)
            self.start()

    def _on_message(self, ws, message: str) -> None:
        """处理 WebSocket 消息"""
        try:
            raw = json.loads(message)
            data = raw.get("data", raw)
            stream = data.get("e", "")

            if stream == "aggTrade":
                sym = data["s"]
                feed = self._feeds.get(sym)
                if feed:
                    feed.on_agg_trade(data)
                    if self._on_tick:
                        self._on_tick(sym)
            elif stream == "depthUpdate":
                sym = data["s"]
                feed = self._feeds.get(sym)
                if feed:
                    feed.on_depth(data)
            elif stream == "markPriceUpdate":
                sym = data["s"]
                feed = self._feeds.get(sym)
                if feed:
                    feed.on_mark_price(data)
            elif data.get("e") == "kline" and data.get("k", {}).get("x"):
                # K线闭合时更新成交量历史
                sym = data["s"]
                feed = self._feeds.get(sym)
                if feed and "k" in data:
                    vol = float(data["k"]["v"])
                    ts = data["k"]["T"] / 1000.0
                    feed.volume_history.append((ts, vol))

        except Exception as e:
            logger.debug("WS 消息解析异常: %s", e)
