"""
仓位管理 — 数据结构、持久化、崩溃恢复
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from src.core.context import Position
from src.utils.constants import Direction, CloseReason

logger = logging.getLogger("unified_trader.execution.position")


class PositionStore:
    """
    仓位持久化存储 — JSON 文件

    特性：
    - 原子写入 (tmp + rename)
    - 崩溃恢复（启动时重新加载）
    - 与交易所同步（清除已不在交易所的本地记录）
    """

    def __init__(self, file_path: str = "data/positions.json"):
        self.file_path = os.path.abspath(file_path)
        self._positions: dict[str, dict] = {}

    # ═══════ CRUD ═══════

    def add(self, pos: Position) -> None:
        """添加仓位"""
        self._positions[pos.symbol] = self._pos_to_dict(pos)
        self._save()

    def update(self, pos: Position) -> None:
        """更新仓位"""
        self._positions[pos.symbol] = self._pos_to_dict(pos)
        self._save()

    def remove(self, symbol: str) -> Optional[dict]:
        """移除仓位"""
        removed = self._positions.pop(symbol, None)
        if removed:
            self._save()
        return removed

    def get(self, symbol: str) -> Optional[dict]:
        return self._positions.get(symbol)

    def get_all(self) -> dict[str, dict]:
        return dict(self._positions)

    def has(self, symbol: str) -> bool:
        return symbol in self._positions

    @property
    def count(self) -> int:
        return len(self._positions)

    def clear(self) -> None:
        self._positions.clear()
        self._save()

    # ═══════ 持久化 ═══════

    def load(self) -> None:
        """从文件加载"""
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                # 兼容旧格式
                self._positions = {p["symbol"]: p for p in data}
            else:
                self._positions = data
            logger.info("加载 %d 个持仓记录", len(self._positions))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("加载持仓文件失败: %s", e)

    def _save(self) -> None:
        """原子写入"""
        tmp = self.file_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(self._positions, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.file_path)
        except IOError as e:
            logger.warning("保存持仓文件失败: %s", e)

    # ═══════ 转换 ═══════

    @staticmethod
    def _pos_to_dict(pos: Position) -> dict:
        return {
            "symbol": pos.symbol,
            "direction": pos.direction.value,
            "entry_price": pos.entry_price,
            "quantity": pos.quantity,
            "order_usdt": pos.order_usdt,
            "leverage": pos.leverage,
            "sl_price": pos.sl_price,
            "tp_price": pos.tp_price,
            "highest_price": pos.highest_price,
            "lowest_price": pos.lowest_price,
            "trailing_stop_active": pos.trailing_stop_active,
            "entry_time": pos.entry_time,
            "exit_time": pos.exit_time,
            "exit_price": pos.exit_price,
            "exit_reason": pos.exit_reason,
            "signal_snapshot": pos.signal_snapshot,
            "strategy_name": pos.strategy_name,
        }

    @staticmethod
    def dict_to_pos(d: dict) -> Position:
        return Position(
            symbol=d["symbol"],
            direction=Direction(d.get("direction", "LONG")),
            entry_price=d.get("entry_price", 0.0),
            quantity=d.get("quantity", 0.0),
            order_usdt=d.get("order_usdt", 10.0),
            leverage=d.get("leverage", 3),
            sl_price=d.get("sl_price", 0.0),
            tp_price=d.get("tp_price", 0.0),
            highest_price=d.get("highest_price", 0.0),
            lowest_price=d.get("lowest_price", 0.0),
            trailing_stop_active=d.get("trailing_stop_active", False),
            entry_time=d.get("entry_time", time.time()),
            exit_time=d.get("exit_time"),
            exit_price=d.get("exit_price"),
            exit_reason=d.get("exit_reason"),
            signal_snapshot=d.get("signal_snapshot", {}),
            strategy_name=d.get("strategy_name", ""),
        )

    # ═══════ 崩溃恢复 ═══════

    def sync_with_exchange(self, exchange_positions: list[dict]) -> dict:
        """
        与交易所持仓同步：
        - 移除交易所已不存在的本地记录
        - 返回需要恢复的交易所仓位（本地没有的）
        """
        exchange_symbols = {p.get("symbol", "") for p in exchange_positions}

        # 清除本地有但交易所没有的记录
        stale = set(self._positions.keys()) - exchange_symbols
        for sym in stale:
            logger.info("清除过期持仓记录: %s", sym)
            self._positions.pop(sym, None)

        # 找出交易所新增的仓位（本地没有记录）
        new_positions = {}
        local_symbols = set(self._positions.keys())
        for ep in exchange_positions:
            sym = ep.get("symbol", "")
            if sym not in local_symbols:
                new_positions[sym] = ep

        if stale or new_positions:
            self._save()

        return new_positions
