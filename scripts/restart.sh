#!/bin/bash
# 统一交易框架 — 重启脚本
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "=== 拉取最新代码 ==="
git pull

echo "=== 停止旧进程 ==="
bash scripts/stop.sh

echo "=== 清理旧日志 ==="
find logs/ -name "*.log.*" -mtime +7 -delete 2>/dev/null || true

echo "=== 启动新进程 ==="
bash scripts/start.sh

echo "=== 重启完成 ==="
