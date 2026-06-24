"""
策略基类 — 所有策略的抽象接口
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

from src.core.context import SharedContext, TradeSignal, Position


class BaseStrategy(ABC):
    """
    策略插件接口

    每个策略实现此接口即可接入统一框架。策略之间不共享状态，
    通过 SharedContext 读取市场数据，通过 return TradeSignal 表达交易意图。
    """

    def __init__(self, config: dict):
        self.config = config
        self.logger = logging.getLogger(f"unified_trader.strategy.{self.name}")

    # ── 元信息 ──
    @property
    @abstractmethod
    def name(self) -> str:
        """策略唯一名称"""
        ...

    # ── 调度 ──
    @abstractmethod
    def get_interval(self) -> int:
        """返回扫描间隔（秒），如 30=每30秒, 1800=每30分钟"""
        ...

    @abstractmethod
    def get_symbols(self, ctx: SharedContext) -> list[str]:
        """返回此策略关注的币种列表（可动态变化）"""
        ...

    # ── 核心逻辑 ──
    @abstractmethod
    def on_cycle(self, ctx: SharedContext) -> list[TradeSignal]:
        """
        每周期被调度器调用。

        返回 TradeSignal 列表。
        框架会对每个信号执行风控检查，通过的才执行。

        返回空列表 = 本轮无信号。
        返回 NEUTRAL 信号 = 用于记录分析，不执行交易。
        """
        ...

    # ── 生命周期钩子 ──
    def on_start(self, ctx: SharedContext) -> None:
        """策略启动时调用一次"""
        self.logger.info("%s 策略已启动，关注 %d 个币种", self.name, len(self.get_symbols(ctx)))

    def on_stop(self, ctx: SharedContext) -> None:
        """策略停止时调用一次"""
        self.logger.info("%s 策略已停止", self.name)

    def on_position_opened(self, pos: Position) -> None:
        """仓位开仓后回调"""
        pass

    def on_position_closed(self, pos: Position, pnl: float) -> None:
        """仓位平仓后回调"""
        pass

    # ── 风控配置 ──
    def get_risk_profile(self) -> dict:
        """返回策略级风控参数，覆盖全局默认值"""
        return {
            "max_positions": 3,
            "order_usdt_per_symbol": 10,
            "leverage": 3,
            "stop_loss_pct": 15.0,
            "take_profit_pct": 10.0,
            "trailing_stop_activate_pct": 7.0,
            "trailing_stop_callback_pct": 5.0,
            "min_hold_minutes": 120,
            "signal_cooldown_minutes": 5,
        }
