"""
配置加载器 — YAML + Pydantic 验证
"""
import os
from pathlib import Path
from typing import Optional

import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """加载 YAML 配置文件"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path.absolute()}")
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if config is None:
        raise ValueError(f"配置文件为空: {path.absolute()}")
    # 环境变量覆盖
    _apply_env_overrides(config)
    return config


def _apply_env_overrides(config: dict) -> None:
    """用环境变量覆盖敏感配置"""
    env_map = {
        "BINANCE_API_KEY": ("api", "key"),
        "BINANCE_API_SECRET": ("api", "secret"),
        "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
        "TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
    }
    for env_var, (section, key) in env_map.items():
        val = os.environ.get(env_var)
        if val:
            config.setdefault(section, {})[key] = val


def get_strategy_config(config: dict, strategy_name: str) -> dict:
    """获取特定策略的配置"""
    strategies_cfg = config.get("strategies", {})
    return strategies_cfg.get(strategy_name, {})


def get_enabled_strategies(config: dict) -> list[str]:
    """获取启用的策略列表"""
    strategies_cfg = config.get("strategies", {})
    return [
        name for name, cfg in strategies_cfg.items()
        if cfg.get("enabled", False)
    ]
