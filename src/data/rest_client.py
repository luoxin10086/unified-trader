"""
Binance REST API 统一客户端
"""
import logging
import time
from typing import Any, Optional

from binance.um_futures import UMFutures
from binance.spot import Spot as SpotClient

from src.data.cache import DataCache

logger = logging.getLogger("unified_trader.data.rest")


class BinanceRESTClient:
    """
    统一 REST API 客户端 — 封装 UMFutures + Spot

    特性：
    - 自动重试 (3次)
    - 响应缓存 (exchangeInfo, symbol精度)
    - 代理支持
    """

    def __init__(self, config: dict):
        api_cfg = config.get("api", {})
        self.key = api_cfg.get("key", "")
        self.secret = api_cfg.get("secret", "")
        self.testnet = api_cfg.get("testnet", False)
        self.proxy = api_cfg.get("proxy", "")

        # 初始化客户端
        self.futures = UMFutures(
            key=self.key, secret=self.secret,
            base_url="https://testnet.binancefuture.com" if self.testnet
            else "https://fapi.binance.com",
        )
        self.spot = SpotClient(
            api_key=self.key, api_secret=self.secret,
            base_url="https://testnet.binance.vision" if self.testnet
            else "https://api.binance.com",
        )

        self.cache = DataCache(ttl_seconds=3600)
        self._symbol_info: dict[str, dict] = {}

    # ═══════ 通用 ═══════

    def _call(self, func, retries: int = 3, **kwargs) -> Any:
        """自动重试的 API 调用"""
        last_error = None
        for attempt in range(retries):
            try:
                return func(**kwargs)
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 10))
        raise last_error

    # ═══════ 账户 ═══════

    def get_account(self) -> dict:
        """获取账户信息"""
        return self._call(self.futures.account)

    def get_balance(self) -> float:
        """获取 USDT 可用余额"""
        try:
            acct = self.get_account()
            for asset in acct.get("assets", []):
                if asset["asset"] == "USDT":
                    return float(asset.get("availableBalance", 0))
        except Exception:
            pass
        return 0.0

    def get_all_positions(self) -> list[dict]:
        """获取所有持仓"""
        try:
            result = self._call(self.futures.get_position_risk)
            if isinstance(result, list):
                # 过滤有持仓的
                return [p for p in result if float(p.get("positionAmt", 0)) != 0]
        except Exception:
            pass
        return []

    # ═══════ 行情 ═══════

    def get_price(self, symbol: str) -> float:
        """获取当前价格"""
        try:
            t = self._call(self.futures.ticker_price, symbol=symbol)
            return float(t["price"])
        except Exception:
            return 0.0

    def get_prices_batch(self, symbols: list[str]) -> dict[str, float]:
        """批量获取价格"""
        try:
            tickers = self._call(self.futures.ticker_price)
            result = {}
            for t in tickers:
                sym = t.get("symbol", "")
                if sym in symbols:
                    result[sym] = float(t["price"])
            return result
        except Exception:
            return {}

    def get_orderbook(self, symbol: str, limit: int = 20) -> Optional[dict]:
        """获取订单簿"""
        try:
            ob = self._call(self.futures.depth, symbol=symbol, limit=limit)
            return {
                "bids": [[float(p), float(q)] for p, q in ob.get("bids", [])],
                "asks": [[float(p), float(q)] for p, q in ob.get("asks", [])],
            }
        except Exception:
            return None

    def get_klines(
        self, symbol: str, interval: str = "5m", limit: int = 60,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> list[dict]:
        """获取K线"""
        kwargs = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            kwargs["startTime"] = start_time
        if end_time:
            kwargs["endTime"] = end_time
        try:
            raw = self._call(self.futures.klines, **kwargs)
            return [
                {
                    "open_time": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": k[6],
                    "quote_volume": float(k[7]),
                    "trades": k[8],
                    "taker_buy_volume": float(k[9]),
                    "taker_buy_quote_volume": float(k[10]),
                }
                for k in raw
            ]
        except Exception:
            return []

    def get_24h_ticker(self, symbol: Optional[str] = None) -> list[dict]:
        """获取24小时行情"""
        kwargs = {}
        if symbol:
            kwargs["symbol"] = symbol
        try:
            return self._call(self.futures.ticker_24hr_price_change, **kwargs)
        except Exception:
            return []

    # ═══════ 合约信息 ═══════

    def get_exchange_info(self) -> dict:
        """获取交易所信息（缓存1小时）"""
        cached = self.cache.get("exchange_info")
        if cached:
            return cached
        info = self._call(self.futures.exchange_info)
        self.cache.set("exchange_info", info)
        return info

    def get_symbol_info(self, symbol: str) -> dict:
        """获取 symbol 精度信息"""
        if symbol in self._symbol_info:
            return self._symbol_info[symbol]

        info = self.get_exchange_info()
        for s in info.get("symbols", []):
            sym = s.get("symbol", "")
            filters = {}
            for f in s.get("filters", []):
                filters[f["filterType"]] = f

            self._symbol_info[sym] = {
                "symbol": sym,
                "status": s.get("status", ""),
                "onboard_date": s.get("onboardDate", 0),
                "price_precision": s.get("pricePrecision", 2),
                "quantity_precision": s.get("quantityPrecision", 2),
                "tick_size": float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)),
                "step_size": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "min_qty": float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                "min_notional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
            }
        return self._symbol_info.get(symbol, {})

    # ═══════ 大户数据 ═══════

    def get_smart_money(self, symbol: str, period: str = "5m") -> dict:
        """获取大户多空数据"""
        try:
            top_pos = self._call(
                self.futures.top_long_short_position_ratio,
                symbol=symbol, period=period, limit=1
            )
            top_acct = self._call(
                self.futures.top_long_short_account_ratio,
                symbol=symbol, period=period, limit=1
            )
            global_acct = self._call(
                self.futures.long_short_account_ratio,
                symbol=symbol, period=period, limit=1
            )
            taker = self._call(
                self.futures.taker_long_short_ratio,
                symbol=symbol, period=period, limit=1
            )
            return {
                "top_position_ratio": float(top_pos[0]["longShortRatio"]) if top_pos else 1.0,
                "top_account_ratio": float(top_acct[0]["longShortRatio"]) if top_acct else 1.0,
                "global_account_ratio": float(global_acct[0]["longShortRatio"]) if global_acct else 1.0,
                "taker_buy_sell_ratio": float(taker[0]["buySellRatio"]) if taker else 1.0,
            }
        except Exception as e:
            logger.debug("获取大户数据失败 %s: %s", symbol, e)
            return {}

    # ═══════ 资金费率 ═══════

    def get_funding_rate(self, symbol: str) -> float:
        """获取当前资金费率"""
        try:
            result = self._call(self.futures.funding_rate, symbol=symbol, limit=1)
            if result:
                return float(result[0].get("fundingRate", 0))
        except Exception:
            pass
        return 0.0

    def get_all_funding_rates(self) -> dict[str, float]:
        """获取所有币种的资金费率"""
        try:
            result = self._call(self.futures.funding_info)
            return {
                r["symbol"]: float(r.get("lastFundingRate", 0))
                for r in result if r.get("symbol")
            }
        except Exception:
            return {}

    # ═══════ OI ═══════

    def get_open_interest(self, symbol: str) -> float:
        """获取持仓量"""
        try:
            result = self._call(self.futures.open_interest, symbol=symbol)
            return float(result.get("openInterest", 0))
        except Exception:
            return 0.0

    # ═══════ 交易 ═══════

    def get_recent_trades(self, symbol: str, limit: int = 100) -> list[dict]:
        """获取最近成交"""
        try:
            return self._call(self.futures.agg_trades, symbol=symbol, limit=limit)
        except Exception:
            return []

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """设置杠杆"""
        try:
            self._call(self.futures.change_leverage, symbol=symbol, leverage=leverage)
            return True
        except Exception as e:
            logger.warning("设置杠杆失败 %s %dx: %s", symbol, leverage, e)
            return False

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> bool:
        """设置保证金模式"""
        try:
            self._call(self.futures.change_margin_type, symbol=symbol, marginType=margin_type)
            return True
        except Exception as e:
            if "No need to change" not in str(e):
                logger.warning("设置保证金模式失败 %s: %s", symbol, e)
            return False

    def place_order(
        self, symbol: str, side: str, order_type: str = "MARKET",
        quantity: float = 0, price: float = 0, **kwargs
    ) -> Optional[dict]:
        """下单"""
        params = {"symbol": symbol, "side": side, "type": order_type}
        if order_type == "MARKET":
            params["quantity"] = str(quantity)
        else:
            params["quantity"] = str(quantity)
            params["price"] = str(round(price, 4))
            params["timeInForce"] = "GTC"
        params.update(kwargs)
        try:
            return self._call(self.futures.new_order, **params)
        except Exception as e:
            logger.error("下单失败 %s %s: %s", symbol, side, e)
            return None

    def cancel_all_orders(self, symbol: str) -> bool:
        """取消所有挂单"""
        try:
            self._call(self.futures.cancel_open_orders, symbol=symbol)
            return True
        except Exception:
            return False
