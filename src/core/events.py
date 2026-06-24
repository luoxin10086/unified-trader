"""
事件总线 — 组件间松耦合通信
"""
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("unified_trader.events")


class EventBus:
    """轻量级发布/订阅事件总线"""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        """订阅事件"""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        """取消订阅"""
        if event_type in self._handlers:
            self._handlers[event_type].remove(handler)

    def emit(self, event_type: str, data: Any = None) -> None:
        """发送事件（同步，异常不中断其他 handler）"""
        for handler in self._handlers.get(event_type, []):
            try:
                handler(data)
            except Exception:
                logger.warning(
                    f"事件处理器异常 event={event_type} handler={handler.__name__}",
                    exc_info=True,
                )

    def clear(self) -> None:
        """清除所有订阅"""
        self._handlers.clear()


# 预定义事件类型
class Events:
    # 系统
    ENGINE_STARTED = "engine.started"
    ENGINE_STOPPING = "engine.stopping"
    ENGINE_STOPPED = "engine.stopped"

    # 数据
    MARKET_DATA_UPDATE = "data.market_update"       # MarketData
    SMART_MONEY_UPDATE = "data.smart_money_update"   # SmartMoneyData

    # 策略
    SIGNAL_GENERATED = "signal.generated"            # TradeSignal
    SIGNAL_REJECTED = "signal.rejected"              # TradeSignal + reason

    # 仓位
    POSITION_OPENED = "position.opened"              # Position
    POSITION_CLOSED = "position.closed"              # Position + pnl
    POSITION_STOPPED = "position.stopped"            # Position + CloseReason

    # 风控
    RISK_LIMIT_HIT = "risk.limit_hit"                # reason
    ACCOUNT_STOP = "risk.account_stop"               # loss_pct

    # 定时
    HOURLY = "timer.hourly"
    DAILY = "timer.daily"
