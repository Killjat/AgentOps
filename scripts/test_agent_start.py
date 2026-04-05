#!/usr/bin/env python3
"""手动测试远程 Agent 的启动"""
import asyncio
import asyncssh

async def test_agent_start():
    """测试 Agent 启动"""
    conn = await asyncssh.connect(
        host="165.154.235.9",
        port=22,
        username="root",
        password="kqvfhpsiq@099211",
        known_hosts=None,
    )
    
    try:
        print("=== 1. 停止旧进程 ===")
        await conn.run("pkill -f 'agent.py' 2>/dev/null", check=False)
        await asyncio.sleep(1)
        
        print("\n=== 2. 检查目录结构 ===")
        result = await conn.run("ls -la /opt/agentops/", check=False)
        print(result.stdout)
        
        print("\n=== 3. 测试 Python 导入 ===")
        result = await conn.run("cd /opt/agentops && python3 -c 'import agent; print(\"导入成功\")' 2>&1", check=False)
        print(f"导入测试: {result.stdout}")
        if result.stderr:
            print(f"导入错误: {result.stderr}")
        
        print("\n=== 4. 测试模块执行 ===")
        result = await conn.run("cd /opt/agentops && python3 -m agent --help 2>&1", check=False)
        print(f"帮助信息: {result.stdout[:200]}")
        if result.stderr:
            print(f"错误: {result.stderr[:200]}")
        
        print("\n=== 5. 手动启动 Agent (前台模式) ===")
        start_cmd = "cd /opt/agentops && timeout 5 python3 -m agent --host 0.0.0.0 --port 9000 --agent-id test --type linux 2>&1"
        result = await conn.run(start_cmd, check=False)
        print(f"启动输出: {result.stdout}")
        if result.stderr:
            print(f"启动错误: {result.stderr}")
        
        print("\n=== 6. 检查进程 ===")
        result = await conn.run("ps aux | grep 'python.*agent' | grep -v grep", check=False)
        if result.stdout:
            print(f"进程: {result.stdout}")
        else:
            print("未找到进程")
        
        print("\n=== 7. 检查端口 ===")
        result = await conn.run("netstat -tlnp 2>/dev/null | grep 9000 || ss -tlnp | grep 9000", check=False)
        if result.stdout:
            print(f"端口: {result.stdout}")
        else:
            print("端口 9000 未监听")
        
        print("\n=== 8. 测试 HTTP 端点 ===")
        result = await conn.run("curl -s -m 3 http://127.0.0.1:9000/ping 2>&1 || echo '连接失败'", check=False)
        print(f"Ping 响应: {result.stdout}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(test_agent_start())
