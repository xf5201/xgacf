#!/bin/bash

# 启动脚本

# 检查并激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "Starting bot..."
    
# 启动主程序
python3 main.py
    
EXIT_CODE=$?
echo "Bot process stopped with exit code: $EXIT_CODE"
    
# Exit if manually stopped (Ctrl+C)
if [ $EXIT_CODE -eq 130 ]; then
    echo "Manual stop detected, exiting."
fi
