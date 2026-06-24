"""
风险管理器 — 账户级 + 策略级风控
"""
import logging
import time
from typing import Optional

from src.core.context import SharedContext, TradeSignal, Position
from src.core.events import EventBus, Events
from src.risk.filters import SignalFilters

logger = logging.getLogger("unified_trader.risk")


class RiskManager:
    """
    账户级风险控制

    职责：
    1. 每日亏损上限
    2. 每日交易次数上限
    3. 连续亏损熔断
    4. 账户总亏损止损
    5. 信号入场前过滤
    """

    def __init__(self, ctx: SharedContext, events: EventBus, config: dict):
        self.ctx = ctx
        self.events = events
        self.config = config

        # 账户风控
        acct_risk = config.get("account_risk", {})
        self.max_loss_pct = acct_risk.get("max_loss_pct", 15.0) / 100
        self.max_consecutive_losses = acct_risk.get("max_consecutive_losses", 3)

        # 日风控
        daily_risk = config.get("daily_risk", {})
        self.max_daily_loss = daily_risk.get("max_loss_usdt", 15)
        self.max_daily_trades = daily_risk.get("max_trades_per_day", 20)

        # 入场过滤
        self.filters = SignalFilters(config)

        # 账户初始余额（用于计算亏损比例）
        self._initial_balance: float = 0.0
        self._first_check_done = False

    # ═══════ 信号过滤 ═══════

    def check_signal(self, signal: TradeSignal,
                     btc_change_pct: float = 0.0,
                     btc_trend_bias: float = 0.0,
                     oi_delta_pct: float = 0.0) -> tuple[bool, str]:
        """检查信号是否可以通过风控"""
        return self.filters.check_all(
            self.ctx, signal.symbol,
            btc_change_pct=btc_change_pct,
            btc_trend_bias=btc_trend_bias,
            oi_delta_pct=oi_delta_pct,
        )

    # ═══════ 账户检查 ═══════

    def check_account(self) -> tuple[bool, str]:
        """
        每次入场前检查账户级风控
        Returns: (allowed, reason)
        """
        ctx = self.ctx

        # 1. 账户暂停
        if ctx.account_paused:
            return False, f"账户已暂停: {ctx.account_pause_reason}"

        # 2. 日交易上限
        if ctx.daily_trades >= self.max_daily_trades:
            return False, f"日交易次数达上限: {ctx.daily_trades}/{self.max_daily_trades}"

        # 3. 日亏损上限
        if ctx.daily_pnl < -self.max_daily_loss:
            return False, f"日亏损达上限: {ctx.daily_pnl:.2f}U < {-self.max_daily_loss}U"

        # 4. 连续亏损
        if ctx.consecutive_losses >= self.max_consecutive_losses:
            ctx.account_paused = True
            ctx.account_pause_reason = f"连续亏损{ctx.consecutive_losses}次，暂停交易"
            self.events.emit(Events.ACCOUNT_STOP, {"reason": ctx.account_pause_reason})
            return False, ctx.account_pause_reason

        # 5. 账户总亏损
        if self._initial_balance > 0:
            loss_pct = (self._initial_balance - ctx.wallet_balance) / self._initial_balance
            if loss_pct >= self.max_loss_pct:
                ctx.account_paused = True
                ctx.account_pause_reason = f"账户亏损 {loss_pct:.1%} >= {self.max_loss_pct:.0%}"
                self.events.emit(Events.ACCOUNT_STOP, {"reason": ctx.account_pause_reason})
                return False, ctx.account_pause_reason

        return True, ""

    def update_balance(self, wallet: float, available: float, margin: float, unrealized: float) -> None:
        """更新余额快照"""
        ctx = self.ctx
        if not self._first_check_done and wallet > 0:
            self._initial_balance = wallet
            self._first_check_done = True
            logger.info("初始余额: %.2f U", self._initial_balance)
        ctx.wallet_balance = wallet
        ctx.available_balance = available
        ctx.margin_used = margin
        ctx.unrealized_pnl = unrealized

    # ═══════ 仓位检查 ═══════

    def check_position_count(self, strategy_name: str, max_positions: int) -> tuple[bool, str]:
        """检查策略是否达到最大持仓数"""
        my_count = sum(
            1 for p in self.ctx.get_all_positions().values()
            if p.strategy_name == strategy_name
        )
        if my_count >= max_positions:
            return False, f"策略 {strategy_name} 持仓已满: {my_count}/{max_positions}"
        return True, ""
