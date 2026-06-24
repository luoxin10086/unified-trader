"""
数据记录器 — 统一信号、订单、K线、权益记录
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from src.core.context import Position, TradeSignal

logger = logging.getLogger("unified_trader.recording")

MAX_SIGNALS = 5000
MAX_ORDERS = 1000
MAX_EQUITY = 20160  # ~70 days at 5-min intervals


@dataclass
class SignalRecord:
    """信号记录"""
    timestamp: float = field(default_factory=time.time)
    datetime: str = ""
    symbol: str = ""
    direction: str = ""
    score: float = 50.0
    source: str = ""
    reason: str = ""
    # 子分数
    flow_score: float = 0.0
    pattern_score: float = 0.0
    # 资金流
    net_flow: float = 0.0
    large_trade_count: int = 0
    buy_ratio: float = 0.5
    # 特征
    features: dict = field(default_factory=dict)


@dataclass
class OrderRecord:
    """订单记录（已完成）"""
    symbol: str = ""
    direction: str = ""
    strategy: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    entry_time: float = 0.0
    exit_time: float = 0.0
    hold_minutes: float = 0.0
    exit_reason: str = ""
    signal_snapshot: dict = field(default_factory=dict)


@dataclass
class EquityRecord:
    """权益快照"""
    timestamp: float = field(default_factory=time.time)
    wallet: float = 0.0
    available: float = 0.0
    margin: float = 0.0
    unrealized_pnl: float = 0.0
    position_count: int = 0


class DataRecorder:
    """
    统一数据记录器
    - signals.json — 所有信号（上限 5000 条）
    - orders.json — 已平仓订单（上限 1000 条）
    - equity.json — 权益快照（每 5 分钟，上限 20160 条）
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.signals_file = self.data_dir / "signals" / "signals.json"
        self.orders_file = self.data_dir / "orders" / "orders.json"
        self.equity_file = self.data_dir / "equity" / "equity.json"

        for d in [self.signals_file.parent, self.orders_file.parent, self.equity_file.parent]:
            d.mkdir(parents=True, exist_ok=True)

    # ═══════ 信号 ═══════

    def record_signal(self, signal: TradeSignal) -> None:
        """记录一个信号"""
        record = SignalRecord(
            timestamp=time.time(),
            datetime=time.strftime("%Y-%m-%d %H:%M:%S"),
            symbol=signal.symbol,
            direction=signal.direction.value,
            score=signal.score,
            source=signal.source,
            reason=signal.reason,
            flow_score=signal.sub_scores.get("flow", 0),
            pattern_score=signal.sub_scores.get("pattern", 0),
            net_flow=signal.features.get("net_flow", 0),
            large_trade_count=signal.features.get("large_trade_count", 0),
            buy_ratio=signal.features.get("buy_ratio", 0.5),
            features=signal.features,
        )
        self._append_json(self.signals_file, asdict(record), MAX_SIGNALS)

    # ═══════ 订单 ═══════

    def record_order(self, pos: Position, pnl: float, pnl_pct: float, reason: str) -> None:
        """记录平仓订单"""
        hold_min = (pos.exit_time - pos.entry_time) / 60 if pos.exit_time else 0
        record = OrderRecord(
            symbol=pos.symbol,
            direction=pos.direction.value,
            strategy=pos.strategy_name,
            entry_price=pos.entry_price,
            exit_price=pos.exit_price or 0,
            quantity=pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_time=pos.entry_time,
            exit_time=pos.exit_time or time.time(),
            hold_minutes=hold_min,
            exit_reason=reason,
            signal_snapshot=pos.signal_snapshot,
        )
        self._append_json(self.orders_file, asdict(record), MAX_ORDERS)

    # ═══════ 权益 ═══════

    def record_equity(self, wallet: float, available: float, margin: float,
                      unrealized_pnl: float, position_count: int) -> None:
        """记录权益快照"""
        record = EquityRecord(
            wallet=wallet,
            available=available,
            margin=margin,
            unrealized_pnl=unrealized_pnl,
            position_count=position_count,
        )
        self._append_json(self.equity_file, asdict(record), MAX_EQUITY)

    # ═══════ 通用 ═══════

    @staticmethod
    def _append_json(file_path: Path, record: dict, max_items: int) -> None:
        """原子追加 JSON 记录"""
        try:
            # 读取现有
            records = []
            if file_path.exists():
                with open(file_path, "r") as f:
                    records = json.load(f)
                if not isinstance(records, list):
                    records = []

            # 追加
            records.append(record)

            # 截断
            if len(records) > max_items:
                records = records[-max_items:]

            # 原子写入
            tmp = str(file_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(records, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp, file_path)

        except Exception as e:
            logger.warning("记录数据失败 %s: %s", file_path.name, e)
