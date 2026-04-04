#!/usr/bin/env python3
"""本地测试 Agent 模块是否能正确执行"""

import subprocess
import sys
import os

# 切换到 agent 目录
agent_dir = "/Users/jatsmith/agentops/agent"
os.chdir(agent_dir)

print("=== 1. 检查文件 ===")
result = subprocess.run(
    ["ls", "-la", "__init__.py", "__main__.py", "agent.py", "base.py", "linux.py", "windows.py"],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"❌ 文件检查失败: {result.stderr}")
    sys.exit(1)
print(f"✅ 所有文件存在\n{result.stdout}")

print("\n=== 2. 测试模块导入 ===")
result = subprocess.run(
    [sys.executable, "-c", "import agent; print('Agent 模块导入成功')"],
    capture_output=True, text=True
)
if result.returncode != 0 or "Agent 模块导入成功" not in result.stdout:
    print(f"❌ 模块导入失败")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
    sys.exit(1)
print(f"✅ {result.stdout.strip()}")

print("\n=== 3. 测试入口函数 ===")
result = subprocess.run(
    [sys.executable, "-m", "agent", "--help"],
    capture_output=True, text=True
)
if result.returncode != 0 or "usage:" not in result.stdout.lower():
    print(f"❌ 入口函数测试失败")
    print(f"stdout: {result.stdout}")
    print(f"stderr: {result.stderr}")
    sys.exit(1)
print(f"✅ 入口函数工作正常")
print(result.stdout[:200])

print("\n=== ✅ 所有测试通过 ===")
