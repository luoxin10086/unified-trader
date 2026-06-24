"""
共享上下文 — 策略和组件间的共享状态
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.utils.constants import Direction


@dataclass
class MarketData:
    """单个币种的实时市场数据"""
    symbol: str
    timestamp: float = field(default_factory=time.time)

    # WebSocket 数据
    trades: list[dict] = field(default_factory=list)
    orderbook: Optional[dict] = None        # {bids: [[price, qty]...], asks: ...}
    mark_price: float = 0.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    liquidations: list[dict] = field(default_factory=list)  # forceOrder events

    # 计算指标
    ob_imbalance_5: float = 0.0
    ob_imbalance_20: float = 0.0
    volume_surge_ratio: float = 1.0
    taker_buy_volume_5min: float = 0.0
    taker_sell_volume_5min: float = 0.0
    large_inflow_duration: float = 0.0
    large_inflow_accumulated: float = 0.0

    # 价格历史 (seconds_ago -> price)
    price_history: dict[int, float] = field(default_factory=dict)

    # 大单
    large_orders: list[dict] = field(default_factory=list)


@dataclass
class SmartMoneyData:
    """大户数据"""
    symbol: str
    timestamp: float = field(default_factory=time.time)
    top_position_ratio: float = 1.0      # 大户持仓多空比
    top_account_ratio: float = 1.0       # 大户账户多空比
    global_account_ratio: float = 1.0    # 全局账户多空比
    taker_buy_sell_ratio: float = 1.0    # 主动买卖比


@dataclass
class TradeSignal:
    """策略产生的交易信号"""
    symbol: str
    direction: Direction
    score: float = 50.0                  # 0-100 综合评分
    sub_scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    source: str = ""                     # 策略名称
    features: dict[str, Any] = field(default_factory=dict)  # 特征快照


@dataclass
class Position:
    """仓位"""
    symbol: str
    direction: Direction
    entry_price: float
    quantity: float
    order_usdt: float = 10.0
    leverage: int = 3
    sl_price: float = 0.0
    tp_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_stop_active: bool = False
    entry_time: float = field(default_factory=time.time)
    exit_time: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    signal_snapshot: dict[str, Any] = field(default_factory=dict)
    strategy_name: str = ""

    @property
    def unrealized_pnl(self) -> float:
        return 0.0  # 需要当前价格计算，由 OrderManager 负责


class SharedContext:
    """
    全局共享上下文 — 所有策略和组件只读访问
    线程安全
    """

    def __init__(self):
        self._lock = threading.RLock()

        # 配置
        self.config: dict = {}
        self.dry_run: bool = True

        # 市场数据 {symbol: MarketData}
        self._market_data: dict[str, MarketData] = {}

        # 大户数据 {symbol: SmartMoneyData}
        self._smart_money: dict[str, SmartMoneyData] = {}

        # 仓位 {symbol: Position}
        self._positions: dict[str, Position] = {}

        # 风控状态
        self.account_paused: bool = False
        self.account_pause_reason: str = ""
        self.daily_trades: int = 0
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.last_trade_time: float = 0.0

        # 账户
        self.wallet_balance: float = 0.0
        self.available_balance: float = 0.0
        self.margin_used: float = 0.0
        self.unrealized_pnl: float = 0.0

    # ── MarketData ──
    def get_market(self, symbol: str) -> Optional[MarketData]:
        with self._lock:
            return self._market_data.get(symbol)

    def get_all_markets(self) -> dict[str, MarketData]:
        with self._lock:
            return dict(self._market_data)

    def update_market(self, data: MarketData) -> None:
        with self._lock:
            self._market_data[data.symbol] = data

    def get_all_symbols(self) -> list[str]:
        with self._lock:
            return list(self._market_data.keys())

    # ── SmartMoney ──
    def get_smart_money(self, symbol: str) -> Optional[SmartMoneyData]:
        with self._lock:
            return self._smart_money.get(symbol)

    def update_smart_money(self, data: SmartMoneyData) -> None:
        with self._lock:
            self._smart_money[data.symbol] = data

    # ── Positions ──
    def get_position(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.get(symbol)

    def get_all_positions(self) -> dict[str, Position]:
        with self._lock:
            return dict(self._positions)

    def add_position(self, pos: Position) -> None:
        with self._lock:
            self._positions[pos.symbol] = pos

    def remove_position(self, symbol: str) -> None:
        with self._lock:
            self._positions.pop(symbol, None)

    @property
    def position_count(self) -> int:
        with self._lock:
            return len(self._positions)

    # ── 状态检查 ──
    def is_trading_allowed(self) -> bool:
        """检查是否允许交易"""
        with self._lock:
            if self.account_paused:
                return False
            if self.daily_trades >= self.config.get("daily_risk", {}).get("max_trades_per_day", 20):
                return False
            return True
