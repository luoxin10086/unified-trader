#!/bin/bash
# 统一交易框架 — 启动脚本
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

PID_FILE="$ROOT/data/unified_trader.pid"
LOG_FILE="$ROOT/logs/trading.log"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "框架已在运行中 (PID=$PID)"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

echo "启动统一交易框架..."
nohup python3 main.py >> "$LOG_FILE" 2>&1 &
PID=$!
echo $PID > "$PID_FILE"
echo "已启动 (PID=$PID)"
