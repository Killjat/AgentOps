#!/bin/bash
# 编译 CyberAgent 各平台二进制
# 需要在对应平台上运行

set -e
cd "$(dirname "$0")/agent"

echo "▶ 编译 CyberAgent..."

pyinstaller \
  --onefile \
  --name cyberagent \
  --add-data "*.py:." \
  --hidden-import asyncio \
  --hidden-import websockets \
  --hidden-import websockets.legacy \
  --hidden-import websockets.legacy.client \
  agent.py

echo "✅ 编译完成: agent/dist/cyberagent"
