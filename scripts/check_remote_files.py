#!/usr/bin/env python3
"""检查目标服务器上的 Agent 文件"""
import asyncio
import asyncssh

async def check_remote_files():
    """检查远程服务器上的文件"""
    conn = await asyncssh.connect(
        host="165.154.235.9",
        port=22,
        username="root",
        password="kqvfhpsiq@099211",
        known_hosts=None,
    )
    
    try:
        print("=== 检查 /opt/agentops 目录中的文件 ===")
        result = await conn.run("ls -lah /opt/agentops/", check=False)
        print(result.stdout)
        
        print("\n=== 检查文件内容（前50行）===")
        files = ["agent.py", "base.py", "linux.py", "windows.py", "__main__.py", "__init__.py"]
        for fname in files:
            result = await conn.run(f"head -50 /opt/agentops/{fname} 2>/dev/null || echo '文件不存在'", check=False)
            if result.exit_status == 0:
                print(f"\n--- {fname} (前3行) ---")
                lines = result.stdout.strip().split('\n')[:3]
                for line in lines:
                    print(f"  {line}")
            else:
                print(f"  ✗ {fname}: 文件不存在")
        
        print("\n=== 检查当前运行的 Agent 进程 ===")
        result = await conn.run("ps aux | grep agent.py | grep -v grep", check=False)
        if result.stdout:
            print(result.stdout)
        else:
            print("  ✗ 未找到运行中的 Agent 进程")
        
        print("\n=== 检查端口监听 ===")
        result = await conn.run("netstat -tlnp 2>/dev/null | grep 9000 || ss -tlnp | grep 9000", check=False)
        if result.stdout:
            print(result.stdout)
        else:
            print("  ✗ 端口 9000 未监听")
        
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(check_remote_files())
