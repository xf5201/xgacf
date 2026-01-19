#!/bin/bash
echo "🚀 开始部署 TG Premium 机器人..."

# 更新系统
apt update && apt upgrade -y

# 安装系统编译依赖
apt install -y python3 python3-venv git build-essential libssl-dev libffi-dev libgmp-dev curl cargo

# 创建虚拟环境 (如果不存在)
if [ ! -d "venv" ]; then
    echo "📦 创建 Python 虚拟环境..."
    python3 -m venv venv
fi

# 激活环境并安装 Python 依赖
echo "📥 安装 Python 依赖..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo ""
echo "✅ 环境部署完成！"
echo "⚠️ 下一步: 1. 复制 .env.example 为 .env 并填入密钥  2. bash start.sh (启动)"
