"""
数据缓存 — 内存 + 磁盘双层
"""
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional


class DataCache:
    """
    双层缓存：内存（快速访问）+ JSON 文件（持久化）

    用于缓存不太频繁变化的数据：交易所信息、symbol 精度、历史价格等
    """

    def __init__(self, cache_dir: str = "cache", ttl_seconds: int = 3600):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self._memory: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            # 先查内存
            if key in self._memory:
                ts, val = self._memory[key]
                if time.time() - ts < self.ttl:
                    return val
                del self._memory[key]

        # 再查磁盘
        file_path = self._key_to_path(key)
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                ts = data.get("_ts", 0)
                if time.time() - ts < self.ttl:
                    # 回写内存
                    val = data.get("_val")
                    with self._lock:
                        self._memory[key] = (ts, val)
                    return val
            except (json.JSONDecodeError, IOError):
                pass

        return None

    def set(self, key: str, value: Any) -> None:
        """写入缓存"""
        now = time.time()
        with self._lock:
            self._memory[key] = (now, value)
        # 写磁盘
        file_path = self._key_to_path(key)
        try:
            with open(file_path, "w") as f:
                json.dump({"_ts": now, "_val": value}, f)
        except IOError:
            pass

    def _key_to_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(":", "_")
        return self.cache_dir / f"{safe_key}.json"

    def clear(self) -> None:
        """清除所有缓存"""
        with self._lock:
            self._memory.clear()
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)


class RingBuffer:
    """固定大小的环形缓冲区"""

    def __init__(self, maxlen: int):
        self._buf = deque(maxlen=maxlen)

    def append(self, item: Any) -> None:
        self._buf.append(item)

    def get_all(self) -> list:
        return list(self._buf)

    def get_recent(self, n: int) -> list:
        items = list(self._buf)
        return items[-n:]

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)

    def __bool__(self) -> bool:
        return len(self._buf) > 0
