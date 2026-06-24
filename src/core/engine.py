"""
交易引擎 — 统一框架主控
"""
import logging
import signal
import time
from typing import Optional

from src.core.context import SharedContext
from src.core.events import EventBus, Events
from src.core.scheduler import Scheduler
from src.data.feed_manager import FeedManager
from src.execution.order_manager import OrderManager
from src.recording.recorder import DataRecorder
from src.risk.manager import RiskManager
from src.strategies.base import BaseStrategy
from src.utils.config import load_config, get_enabled_strategies
from src.utils.constants import RunMode, CloseReason
from src.utils.logging import setup_logger


class Engine:
    """统一交易引擎"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config: dict = {}
        self.logger: Optional[logging.Logger] = None

        # 核心组件
        self.ctx: Optional[SharedContext] = None
        self.events: Optional[EventBus] = None
        self.scheduler: Optional[Scheduler] = None
        self.feed: Optional[FeedManager] = None
        self.risk: Optional[RiskManager] = None
        self.order: Optional[OrderManager] = None

        # 数据记录
        self.recorder: Optional[DataRecorder] = None

        # 策略
        self.strategies: dict[str, BaseStrategy] = {}

        # 状态
        self._running: bool = False
        self._start_time: float = 0.0
        self._strategy_tasks: dict[str, str] = {}

        # BTC 追踪
        self._btc_price_5m_ago: float = 0.0
        self._btc_trend_bias: float = 0.0

    @property
    def run_mode(self) -> RunMode:
        return RunMode.DRY_RUN if self.config.get("dry_run", True) else RunMode.LIVE

    # ═══════ 初始化 ═══════

    def setup(self) -> None:
        self.config = load_config(self.config_path)
        self.logger = setup_logger(self.config)
        self.logger.info("═" * 60)
        self.logger.info("统一交易框架 v1.0 启动中...")

        self.ctx = SharedContext()
        self.ctx.config = self.config
        self.ctx.dry_run = self.config.get("dry_run", True)
        self.events = EventBus()
        self.scheduler = Scheduler(tick_interval=1.0)

        # 数据层
        self.feed = FeedManager(self.ctx, self.config)

        # 风控
        self.risk = RiskManager(self.ctx, self.events, self.config)

        # 执行层
        self.order = OrderManager(self.ctx, self.events, self.feed.rest, self.config)

        # 数据记录
        self.recorder = DataRecorder(self.config.get("data_dir", "data"))
        self.logger.info("数据记录器已初始化: %s", self.recorder.data_dir)

        # 策略
        self._load_strategies()

        # 事件注册
        self.events.subscribe(Events.SIGNAL_GENERATED, self._on_signal)
        self.events.subscribe(Events.POSITION_OPENED, self._on_position_opened)
        self.events.subscribe(Events.POSITION_CLOSED, self._on_position_closed)
        self.events.subscribe(Events.RISK_LIMIT_HIT, self._on_risk_limit)

        # 系统任务
        self.scheduler.add("system.hourly", self._on_hourly, 3600)
        self.scheduler.add("system.daily_check", self._on_daily_check, 86400)
        self.scheduler.add("system.refresh_symbols", self._refresh_strategy_symbols, 300)
        self.scheduler.add("system.account_sync", self._sync_account, 300)  # 余额同步
        self.scheduler.add("system.exit_check", self._check_exits, 30)      # 止损检查
        self.scheduler.add("system.equity_snapshot", self._equity_snapshot, 300)  # 权益快照

        self.logger.info(
            "引擎初始化完成 | 模式=%s | 策略=%s | 币种=%d",
            self.run_mode.value,
            ", ".join(self.strategies.keys()) if self.strategies else "无",
            len(self.feed.get_aggregated_symbols()),
        )

    def _load_strategies(self) -> None:
        from src.strategies.new_coin.strategy import NewCoinStrategy
        from src.strategies.trend_following.strategy import TrendFollowingStrategy

        strategy_registry = {
            "new_coin": NewCoinStrategy,
            "trend_following": TrendFollowingStrategy,
        }

        enabled = get_enabled_strategies(self.config)
        for name in enabled:
            cls = strategy_registry.get(name)
            if cls is None:
                self.logger.warning("未知策略: %s", name)
                continue

            strategy_cfg = self.config.get("strategies", {}).get(name, {})
            instance = cls(strategy_cfg)
            self.strategies[name] = instance

            # 注入 REST 客户端（策略需要做 API 查询）
            if hasattr(instance, "set_rest_client"):
                instance.set_rest_client(self.feed.rest)

            symbols = instance.get_symbols(self.ctx)
            if symbols:
                self.feed.register_strategy(name, symbols)

            task_name = f"strategy.{name}"
            self.scheduler.add(
                task_name,
                lambda s=instance: self._run_strategy_cycle(s),
                instance.get_interval(),
            )
            self._strategy_tasks[name] = task_name
            self.logger.info("策略: %s (每 %ds, %d 币种)", name, instance.get_interval(), len(symbols))

    # ═══════ 主循环 ═══════

    def run(self) -> None:
        if not self.ctx or not self.scheduler or not self.feed or not self.order:
            self.logger.error("引擎未初始化")
            return

        self._running = True
        self._start_time = time.time()

        def _shutdown(sig, frame):
            self.logger.info("收到退出信号 (%d)", sig)
            self._running = False
        try:
            signal.signal(signal.SIGINT, _shutdown)
            signal.signal(signal.SIGTERM, _shutdown)
        except ValueError:
            pass

        # 启动数据层
        self.feed.start()

        # 崩溃恢复
        self.order.recover_from_exchange()

        # WS 预热
        self.logger.info("等待 WS 数据预热 (5s)...")
        time.sleep(5)

        # 策略启动
        for strategy in self.strategies.values():
            strategy.on_start(self.ctx)

        self.events.emit(Events.ENGINE_STARTED, {
            "strategies": list(self.strategies.keys()),
            "mode": self.run_mode.value,
        })

        self.logger.info("主循环启动 | 策略=%d | 币种=%d", len(self.strategies), len(self.feed.get_aggregated_symbols()))

        loop_count = 0
        try:
            while self._running:
                loop_start = time.time()
                self.scheduler.tick()
                loop_count += 1
                time.sleep(max(0.05, 1.0 - (time.time() - loop_start)))
        except KeyboardInterrupt:
            self.logger.info("键盘中断")
        finally:
            self._shutdown()

    # ═══════ 信号处理 ═══════

    def _run_strategy_cycle(self, strategy: BaseStrategy) -> None:
        """策略周期：生成信号 → 风险检查 → 执行"""
        try:
            signals = strategy.on_cycle(self.ctx)
            for sig in signals:
                if sig.direction.value == "NEUTRAL":
                    continue
                sig.source = strategy.name
                # 直接在此处理，而不是发事件（减少延迟）
                self._process_signal(sig, strategy)
        except Exception:
            self.logger.error("策略 %s 周期异常", strategy.name, exc_info=True)

    def _on_signal(self, data: dict) -> None:
        """处理来自 EventBus 的信号（备用路径）"""
        sig = data.get("signal")
        strategy_name = data.get("strategy", "")
        strategy = self.strategies.get(strategy_name)
        if sig and strategy:
            self._process_signal(sig, strategy)

    def _process_signal(self, sig, strategy: BaseStrategy) -> bool:
        """信号处理管道：过滤 → 账户检查 → 执行"""
        sym = sig.symbol

        # 1. 策略的风控配置
        risk_profile = strategy.get_risk_profile()

        # 2. 持仓数检查
        max_pos = risk_profile.get("max_positions", 3)
        ok, reason = self.risk.check_position_count(strategy.name, max_pos)
        if not ok:
            self.logger.info("[%s] %s", sym, reason)
            return False

        # 3. 信号过滤器
        ok, reason = self.risk.check_signal(
            sig,
            btc_change_pct=0.0,
            btc_trend_bias=self._btc_trend_bias,
        )
        if not ok:
            self.logger.info("[%s] 信号过滤: %s", sym, reason)
            return False

        # 4. 账户检查
        ok, reason = self.risk.check_account()
        if not ok:
            self.logger.info("[%s] 账户风控: %s", sym, reason)
            return False

        # 5. 执行入场
        pos = self.order.open_position(sig, strategy.name, risk_profile)

        # 6. 记录信号
        if self.recorder:
            self.recorder.record_signal(sig)

        return pos is not None

    # ═══════ 系统循环 ═══════

    def _check_exits(self) -> None:
        """检查止损/止盈/移动止损"""
        exits = self.order.check_exits()
        for sym, reason, price in exits:
            self.order.close_position(sym, reason, price)

    def _sync_account(self) -> None:
        """同步账户余额"""
        try:
            acct = self.feed.rest.get_account()
            wallet = float(acct.get("totalWalletBalance", 0))
            avail = float(acct.get("availableBalance", 0))
            margin = float(acct.get("totalPositionInitialMargin", 0))
            upnl = float(acct.get("totalUnrealizedProfit", 0))
            self.risk.update_balance(wallet, avail, margin, upnl)
        except Exception:
            pass

    def _equity_snapshot(self) -> None:
        """记录权益快照"""
        if not self.recorder:
            return
        try:
            acct = self.feed.rest.get_account()
            wallet = float(acct.get("totalWalletBalance", 0))
            avail = float(acct.get("availableBalance", 0))
            margin = float(acct.get("totalPositionInitialMargin", 0))
            upnl = float(acct.get("totalUnrealizedProfit", 0))
            self.recorder.record_equity(wallet, avail, margin, upnl, self.ctx.position_count)
        except Exception:
            pass

    def _refresh_strategy_symbols(self) -> None:
        for name, strategy in self.strategies.items():
            try:
                self.feed.update_strategy_symbols(name, strategy.get_symbols(self.ctx))
            except Exception:
                pass

    # ═══════ 事件 ═══════

    def _on_position_opened(self, data: dict) -> None:
        pos = data.get("position")
        if pos and pos.strategy_name in self.strategies:
            self.strategies[pos.strategy_name].on_position_opened(pos)

    def _on_position_closed(self, data: dict) -> None:
        pos = data.get("position")
        pnl = data.get("pnl", 0.0)
        reason = data.get("reason", "manual")
        if pos and pos.strategy_name in self.strategies:
            self.strategies[pos.strategy_name].on_position_closed(pos, pnl)

        # 记录订单
        if self.recorder and pos:
            pnl_pct = (pos.exit_price - pos.entry_price) / pos.entry_price * 100 if pos.exit_price and pos.entry_price else 0
            self.recorder.record_order(pos, pnl, pnl_pct, reason)

    def _on_risk_limit(self, data: dict) -> None:
        reason = data.get("reason", "未知")
        self.logger.warning("风控触发: %s", reason)

    def _on_hourly(self) -> None:
        self.logger.info(
            "[小时统计] 策略=%d | 持仓=%d | 日交易=%d | WS=%s",
            len(self.strategies),
            self.ctx.position_count,
            self.ctx.daily_trades,
            "alive" if self.feed.is_ws_alive() else "dead",
        )

    def _on_daily_check(self) -> None:
        if self.ctx:
            self.ctx.daily_trades = 0
            self.ctx.daily_pnl = 0.0
            self.logger.info("[每日重置]")

    # ═══════ 退出 ═══════

    def stop(self) -> None:
        self._running = False

    def _shutdown(self) -> None:
        self.logger.info("正在停止引擎...")
        self.events.emit(Events.ENGINE_STOPPING, {"uptime": time.time() - self._start_time})

        for strategy in self.strategies.values():
            strategy.on_stop(self.ctx)

        if self.feed:
            self.feed.stop()

        if self.ctx and self.ctx.position_count > 0:
            self.logger.info("持仓 %d 个已记录: %s", self.ctx.position_count, list(self.ctx.get_all_positions().keys()))

        self.events.emit(Events.ENGINE_STOPPED, {})
        self.logger.info("引擎安全停止")
        self.logger.info("═" * 60)
