#!/bin/bash
# CyberAgentOps USB Launcher - macOS / Linux
# 插入 U 盘后双击运行，自动连接到控制服务器

SERVER_URL="https://april-outermost-undefeatedly.ngrok-free.dev"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 CyberAgentOps Agent 启动中..."
echo "   服务器: $SERVER_URL"

# 检测系统
OS=$(uname -s)
ARCH=$(uname -m)

if [ "$OS" = "Darwin" ]; then
    BIN="$SCRIPT_DIR/bin/cyberagent-mac"
elif [ "$OS" = "Linux" ]; then
    BIN="$SCRIPT_DIR/bin/cyberagent-linux"
else
    echo "❌ 不支持的系统: $OS"
    exit 1
fi

if [ ! -f "$BIN" ]; then
    echo "❌ 找不到 agent 二进制: $BIN"
    echo "   请先运行 build_usb.sh 打包"
    exit 1
fi

chmod +x "$BIN"

# 后台运行，日志写到 U 盘
"$BIN" --server "$SERVER_URL" >> "$SCRIPT_DIR/agent.log" 2>&1 &
echo "✅ Agent 已在后台启动 (PID: $!)"
echo "   日志: $SCRIPT_DIR/agent.log"
