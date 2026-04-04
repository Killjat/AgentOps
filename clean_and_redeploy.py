#!/usr/bin/env python3
"""彻底清理并重新部署 Agent"""
import asyncio
import asyncssh
from pathlib import Path

SERVER_HOST = "165.154.235.9"
SERVER_PORT = 22
SERVER_USER = "root"
SERVER_PASSWORD = "kqvfhpsiq@099211"
AGENT_DIR = "/opt/agentops"

async def clean_and_redploy():
    """清理旧进程并重新部署"""
    conn = await asyncssh.connect(
        host=SERVER_HOST,
        port=SERVER_PORT,
        username=SERVER_USER,
        password=SERVER_PASSWORD,
        known_hosts=None,
    )
    
    try:
        print("=== 1. 停止所有相关进程 ===")
        # 停止所有包含 agent 的 Python 进程
        result = await conn.run("pkill -9 -f 'agent' 2>/dev/null || true", check=False)
        print(f"停止进程: {result.exit_status}")
        
        # 检查端口 9000 是否被占用
        result = await conn.run("lsof -ti:9000 2>/dev/null || echo '端口空闲'", check=False)
        if result.stdout.strip() and result.stdout.strip() != "端口空闲":
            print(f"发现占用端口的进程: {result.stdout}")
            await conn.run(f"kill -9 {result.stdout.strip()} 2>/dev/null || true", check=False)
        
        await asyncio.sleep(2)
        
        print("\n=== 2. 检查目录中的文件 ===")
        result = await conn.run(f"ls -lah {AGENT_DIR}/", check=False)
        print(result.stdout)
        
        print("\n=== 3. 检查是否有旧的单文件 agent.py ===")
        result = await conn.run(f"head -20 {AGENT_DIR}/agent.py", check=False)
        print("agent.py 内容（前20行）:")
        print(result.stdout)
        
        print("\n=== 4. 手动测试模块导入 ===")
        result = await conn.run(
            f"cd {AGENT_DIR} && python3 -c 'import sys; sys.path.insert(0, \".\"); from linux import LinuxAgent; print(\"导入成功\")' 2>&1",
            check=False
        )
        print(f"导入测试: {result.stdout}")
        if result.stderr:
            print(f"错误: {result.stderr}")
        
        print("\n=== 5. 前台运行 Agent（查看输出） ===")
        start_cmd = f"cd {AGENT_DIR} && timeout 5 python3 -m agent --host 0.0.0.0 --port 9000 --agent-id test --type linux 2>&1 || echo '退出'"
        result = await conn.run(start_cmd, check=False)
        print(f"启动输出:\n{result.stdout}")
        
        print("\n=== 6. 检查进程 ===")
        result = await conn.run("ps aux | grep -E 'python.*agent|agent.*py' | grep -v grep", check=False)
        print(f"进程: {result.stdout if result.stdout else '无进程'}")
        
        print("\n=== 7. 检查端口 ===")
        result = await conn.run("netstat -tlnp 2>/dev/null | grep 9000 || ss -tlnp | grep 9000 || echo '端口未监听'", check=False)
        print(f"端口: {result.stdout}")
        
        print("\n=== 8. 测试 HTTP 端点 ===")
        result = await conn.run("curl -s -m 3 http://127.0.0.1:9000/ping 2>&1 || echo '无法连接'", check=False)
        print(f"Ping 响应: {result.stdout}")
        
        # 如果成功，测试 discover
        if "pong" in result.stdout.lower() or "cyberagentops" in result.stdout.lower():
            print("\n=== 9. 测试 discover 端点 ===")
            result = await conn.run("curl -s -X POST http://127.0.0.1:9000/discover 2>&1 | head -20", check=False)
            print(f"Discover 响应:\n{result.stdout}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(clean_and_redploy())
