#!/bin/bash

# 激活虚拟环境
source venv/bin/activate

    echo "🚀 正在启动机器人..."
    
    # 启动主程序
    python3 main.py
    
    # 程序执行到这里说明退出了（报错或手动停止）
    EXIT_CODE=$?
    echo "⚠️ 机器人进程已停止，退出代码: $EXIT_CODE"
    
    # 如果是手动 Ctrl+C 停止 (Exit Code 130)，则彻底退出
    if [ $EXIT_CODE -eq 130 ]; then
        echo "🛑 检测到手动停止信号，结束运行。"
       
    
