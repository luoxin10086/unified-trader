#!/bin/bash
# 统一交易框架 — 停止脚本
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

PID_FILE="$ROOT/data/unified_trader.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "PID 文件不存在，尝试查找进程..."
    PIDS=$(ps aux | grep 'python.*main.py' | grep -v grep | awk '{print $2}')
    if [ -n "$PIDS" ]; then
        echo "找到进程: $PIDS"
        kill $PIDS 2>/dev/null
        echo "已发送终止信号"
    else
        echo "未找到运行中的进程"
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    echo "停止 PID=$PID ..."
    kill "$PID"
    sleep 3
    if kill -0 "$PID" 2>/dev/null; then
        echo "强制终止..."
        kill -9 "$PID"
    fi
    echo "已停止"
else
    echo "进程已不存在"
fi

rm -f "$PID_FILE"
