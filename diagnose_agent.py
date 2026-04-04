#!/usr/bin/env python3
"""彻底检查并修复 Agent 启动问题"""
import asyncio
import asyncssh

async def fix_agent():
    """修复 Agent 启动问题"""
    conn = await asyncssh.connect(
        host="165.154.235.9",
        port=22,
        username="root",
        password="kqvfhpsiq@099211",
        known_hosts=None,
    )
    
    try:
        print("=== 1. 停止所有 Python Agent 进程 ===")
        result = await conn.run("pkill -9 -f 'agent' 2>/dev/null; sleep 1", check=False)
        print(f"进程停止: {result.exit_status}")
        
        print("\n=== 2. 检查 /opt/agentops 目录 ===")
        result = await conn.run("ls -lah /opt/agentops/", check=False)
        print(result.stdout)
        
        print("\n=== 3. 检查是否有旧的单文件 agent.py ===")
        result = await conn.run("head -10 /opt/agentops/agent.py", check=False)
        print("agent.py 前10行:")
        print(result.stdout)
        
        print("\n=== 4. 检查是否有 base.py ===")
        result = await conn.run("head -5 /opt/agentops/base.py 2>&1 || echo 'base.py 不存在'", check=False)
        print("base.py 前5行:")
        print(result.stdout)
        
        print("\n=== 5. 测试 Python 模块导入 ===")
        result = await conn.run("cd /opt/agentops && python3 -c 'from linux import LinuxAgent; print(\"导入成功\")' 2>&1", check=False)
        print(f"导入测试: {result.stdout}")
        
        print("\n=== 6. 手动启动 Agent (前台模式，查看错误) ===")
        start_cmd = "cd /opt/agentops && timeout 3 python3 -m agent --host 0.0.0.0 --port 9000 --agent-id test --type linux 2>&1 || echo '超时或退出'"
        result = await conn.run(start_cmd, check=False)
        print(f"启动输出:\n{result.stdout}")
        
        print("\n=== 7. 检查进程 ===")
        result = await conn.run("ps aux | grep -E 'python.*agent|agent.*py' | grep -v grep", check=False)
        print(f"进程: {result.stdout if result.stdout else '无进程'}")
        
        print("\n=== 8. 检查端口 ===")
        result = await conn.run("netstat -tlnp 2>/dev/null | grep 9000 || ss -tlnp | grep 9000 || echo '端口未监听'", check=False)
        print(f"端口: {result.stdout}")
        
        print("\n=== 9. 尝试直接测试 HTTP 端点 ===")
        result = await conn.run("curl -s -m 2 http://127.0.0.1:9000/ping 2>&1 || echo '无法连接'", check=False)
        print(f"HTTP 测试: {result.stdout}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(fix_agent())
