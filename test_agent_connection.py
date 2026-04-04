#!/usr/bin/env python3
"""测试 Agent 连接和扫描功能"""
import asyncio
import asyncssh
from pathlib import Path

SERVER_HOST = "165.154.235.9"
SERVER_PORT = 22
SERVER_USER = "root"
SERVER_PASSWORD = "kqvfhpsiq@099211"
AGENT_PORT = 9000
AGENT_DIR = "/opt/agentops"

async def check_agent_status():
    """检查 Agent 进程和端口状态"""
    conn_kwargs = {
        "host": SERVER_HOST,
        "port": SERVER_PORT,
        "username": SERVER_USER,
        "password": SERVER_PASSWORD,
        "known_hosts": None,
    }
    
    print("=== 连接到服务器 ===")
    conn = await asyncssh.connect(**conn_kwargs)
    
    try:
        # 1. 检查 Agent 进程
        print("\n1. 检查 Agent 进程:")
        result = await conn.run("ps aux | grep 'agent.py' | grep -v grep", check=False)
        if result.stdout:
            print(f"✓ 进程运行中:\n{result.stdout}")
        else:
            print("✗ 未找到运行中的 Agent 进程")
        
        # 2. 检查 Agent 文件
        print("\n2. 检查 Agent 文件:")
        files = ["agent.py", "base.py", "linux.py", "__main__.py"]
        for fname in files:
            result = await conn.run(f"ls -lh {AGENT_DIR}/{fname}", check=False)
            if result.exit_status == 0:
                print(f"✓ {fname}: {result.stdout.strip().split()[4]}")
            else:
                print(f"✗ {fname}: 文件不存在")
        
        # 3. 检查 Agent 日志
        print("\n3. 检查 Agent 日志 (最后 20 行):")
        result = await conn.run(f"tail -20 {AGENT_DIR}/agent.log 2>/dev/null || echo '日志文件不存在'", check=False)
        print(result.stdout if result.stdout else "无日志")
        
        # 4. 检查端口监听
        print("\n4. 检查端口监听:")
        result = await conn.run(f"netstat -tlnp | grep {AGENT_PORT}", check=False)
        if result.stdout:
            print(f"✓ 端口 {AGENT_PORT} 监听中:\n{result.stdout}")
        else:
            print(f"✗ 端口 {AGENT_PORT} 未监听")
        
        # 5. 测试 Agent HTTP 端点
        print("\n5. 测试 Agent HTTP 端点:")
        result = await conn.run(f"curl -s http://127.0.0.1:{AGENT_PORT}/ping 2>/dev/null || echo '连接失败'", check=False)
        if result.stdout:
            print(f"✓ Ping 响应: {result.stdout[:100]}")
        else:
            print("✗ 无法连接到 Agent HTTP 端点")
        
        # 6. 测试 discover 端点
        print("\n6. 测试 discover 端点:")
        result = await conn.run(f"curl -s -X POST http://127.0.0.1:{AGENT_PORT}/discover 2>/dev/null || echo '连接失败'", check=False)
        if result.stdout:
            print(f"✓ Discover 响应: {result.stdout[:200]}")
        else:
            print("✗ 无法调用 discover 端点")
        
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(check_agent_status())
