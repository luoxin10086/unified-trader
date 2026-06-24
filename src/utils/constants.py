"""
全局常量
"""
from enum import Enum
from typing import Literal

# 交易所
EXCHANGE_BINANCE = "binance"
EXCHANGE_FUTURES = "usdt-m"

# 方向
class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"

# 平仓原因
class CloseReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    MANUAL = "MANUAL"
    ACCOUNT_RISK = "ACCOUNT_RISK"
    DAILY_LIMIT = "DAILY_LIMIT"

# 订单类型
class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

# 运行模式
class RunMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"
    BACKTEST = "BACKTEST"

# 默认值
DEFAULT_LEVERAGE = 3
DEFAULT_ORDER_USDT = 10
DEFAULT_MAX_POSITIONS = 3
DEFAULT_LOOP_INTERVAL = 30  # 秒
