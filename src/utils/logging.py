"""
日志系统
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path


def setup_logger(config: dict) -> logging.Logger:
    """配置日志：控制台 + 按日轮转文件"""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
    log_file = log_config.get("file", "logs/trading.log")

    # 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("unified_trader")
    logger.setLevel(level)

    # 防止重复添加 handler
    if logger.handlers:
        return logger

    # 阻止向 root logger 传播（避免重复输出）
    logger.propagate = False

    # 格式
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 按日轮转文件（保留30天）
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """获取已配置的 logger"""
    return logging.getLogger("unified_trader")
