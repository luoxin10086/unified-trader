"""
日志系统
"""
import logging
import os
import sys
from pathlib import Path


def setup_logger(config: dict) -> logging.Logger:
    """配置日志：控制台输出（由 systemd/nohup 负责落盘）"""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)

    # 确保日志目录存在（systemd 不创建目录）
    log_file = log_config.get("file", "logs/trading.log")
    log_dir = os.path.dirname(log_file)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("unified_trader")
    logger.setLevel(level)

    # 防止重复添加 handler
    if logger.handlers:
        return logger

    # 阻止向 root logger 传播
    logger.propagate = False

    # 格式
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出（systemd/nohup 负责重定向到文件）
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    return logger


def get_logger() -> logging.Logger:
    """获取已配置的 logger"""
    return logging.getLogger("unified_trader")
