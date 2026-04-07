#!/bin/bash
# 打包 USB Agent - 将编译好的二进制复制到 usb_agent/bin/
# 运行前需先在各平台编译好 agent（build_agent.sh）

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BIN_DIR="$SCRIPT_DIR/bin"

mkdir -p "$BIN_DIR"

echo "📦 打包 USB Agent..."

# 复制已编译的二进制
copy_bin() {
    local src="$1"
    local dst="$2"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        echo "  ✅ $dst"
    else
        echo "  ⚠️  跳过 $dst（未找到 $src，请先编译）"
    fi
}

copy_bin "$ROOT_DIR/agent/dist/cyberagent"         "$BIN_DIR/cyberagent-mac"
copy_bin "$ROOT_DIR/agent/dist/cyberagent-linux"   "$BIN_DIR/cyberagent-linux"
copy_bin "$ROOT_DIR/agent/dist/cyberagent.exe"     "$BIN_DIR/cyberagent-windows.exe"

chmod +x "$SCRIPT_DIR/launch.sh" 2>/dev/null || true

echo ""
echo "✅ 打包完成，目录结构："
ls -lh "$BIN_DIR" 2>/dev/null || true
echo ""
echo "👉 将整个 usb_agent/ 目录复制到 U 盘根目录即可"
echo "   macOS/Linux 用户运行: launch.sh"
echo "   Windows 用户运行:     launch.bat"
