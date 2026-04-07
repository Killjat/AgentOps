#!/bin/bash
# CyberAgentOps USB Launcher - macOS / Linux
# 插入 U 盘后双击运行，自动连接到控制服务器

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 从 config.txt 读取服务器地址
CONFIG_FILE="$SCRIPT_DIR/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    SERVER_URL=$(grep -E '^SERVER_URL=' "$CONFIG_FILE" | cut -d= -f2-)
fi

if [ -z "$SERVER_URL" ]; then
    echo "❌ 未配置服务器地址，请编辑 config.txt"
    exit 1
fi

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

# macOS 不允许直接从可移动磁盘运行二进制，复制到 /tmp 再执行
LOCAL_BIN="/tmp/cyberagent"
cp "$BIN" "$LOCAL_BIN"
chmod +x "$LOCAL_BIN"
xattr -c "$LOCAL_BIN" 2>/dev/null || true  # 移除 Gatekeeper 隔离标记

# 后台运行，日志写到 U 盘
"$LOCAL_BIN" --server "$SERVER_URL" >> "$SCRIPT_DIR/agent.log" 2>&1 &
echo "✅ Agent 已在后台启动 (PID: $!)"
echo "   日志: $SCRIPT_DIR/agent.log"
