"""
多周期调度器
"""
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger("unified_trader.scheduler")


class ScheduledTask:
    """调度任务"""

    def __init__(
        self,
        name: str,
        callback: Callable[[], None],
        interval_seconds: int,
        run_immediately: bool = False,
    ):
        self.name = name
        self.callback = callback
        self.interval = interval_seconds
        self._last_run: float = 0.0 if run_immediately else time.time()
        self._run_count: int = 0

    @property
    def due(self) -> bool:
        return (time.time() - self._last_run) >= self.interval

    @property
    def last_run(self) -> float:
        return self._last_run

    @property
    def run_count(self) -> int:
        return self._run_count

    def execute(self) -> None:
        """执行任务并更新状态"""
        self._last_run = time.time()
        self._run_count += 1
        self.callback()


class Scheduler:
    """
    多周期任务调度器

    用法:
        scheduler = Scheduler()
        scheduler.add("scan", scan_func, 30)
        scheduler.add("analyze", analyze_func, 1800)

        while running:
            scheduler.tick()
            time.sleep(1)  # 1秒粒度检查
    """

    def __init__(self, tick_interval: float = 1.0):
        self._tasks: dict[str, ScheduledTask] = {}
        self._tick_interval = tick_interval
        self._running = False

    def add(
        self,
        name: str,
        callback: Callable[[], None],
        interval_seconds: int,
        run_immediately: bool = False,
    ) -> ScheduledTask:
        """注册一个周期任务"""
        task = ScheduledTask(name, callback, interval_seconds, run_immediately)
        self._tasks[name] = task
        logger.info(
            "注册任务: %s 每 %ds 执行%s",
            name, interval_seconds,
            " (立即执行)" if run_immediately else "",
        )
        return task

    def remove(self, name: str) -> None:
        """移除一个任务"""
        self._tasks.pop(name, None)

    def tick(self) -> list[str]:
        """
        检查并执行到期任务，返回本次执行的任务名列表
        调用方应在主循环中以 ~1Hz 频率调用
        """
        executed = []
        for name, task in self._tasks.items():
            if task.due:
                try:
                    task.execute()
                    executed.append(name)
                except Exception:
                    logger.error("调度任务异常: %s", name, exc_info=True)
        return executed

    def get_task(self, name: str) -> Optional[ScheduledTask]:
        return self._tasks.get(name)

    def get_status(self) -> dict:
        """返回所有任务状态"""
        now = time.time()
        return {
            name: {
                "last_run": task.last_run,
                "next_run": task.last_run + task.interval,
                "seconds_until_next": max(0, task.last_run + task.interval - now),
                "run_count": task.run_count,
            }
            for name, task in self._tasks.items()
        }
