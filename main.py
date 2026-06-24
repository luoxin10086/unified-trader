"""
统一交易框架入口
"""
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.engine import Engine


def main():
    engine = Engine("config.yaml")
    engine.setup()
    engine.run()


if __name__ == "__main__":
    main()
