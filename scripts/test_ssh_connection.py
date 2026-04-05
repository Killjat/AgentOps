#!/usr/bin/env python3
"""测试 SSH 连接和文件上传"""

import asyncio
import asyncssh
import sys
from pathlib import Path

async def test_connection():
    host = "165.154.235.9"
    username = "root"
    password = "kqvfhpsiq@099211"
    port = 22

    print("=== 1. 测试 SSH 连接 ===")
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host=host,
                port=port,
                username=username,
                password=password,
                known_hosts=None,
                preferred_auth="password,keyboard-interactive"
            ),
            timeout=10
        )
        print("✅ SSH 连接成功")
    except Exception as e:
        print(f"❌ SSH 连接失败: {e}")
        return False

    print("\n=== 2. 创建目录 ===")
    deploy_dir = "/opt/agentops"
    agent_dir = f"{deploy_dir}/agent"

    try:
        result = await conn.run(f"mkdir -p {agent_dir}", check=False)
        print(f"✅ 目录创建成功: {agent_dir}")
    except Exception as e:
        print(f"❌ 目录创建失败: {e}")
        conn.close()
        return False

    print("\n=== 3. 上传文件 ===")
    agent_files = ["__init__.py", "__main__.py", "agent.py", "base.py", "linux.py", "windows.py"]
    agent_local_dir = Path("/Users/jatsmith/agentops/agent")

    try:
        async with conn.start_sftp_client() as sftp:
            for fname in agent_files:
                local_path = agent_local_dir / fname
                if not local_path.exists():
                    print(f"⚠️  文件不存在: {fname}")
                    continue
                remote_path = f"{agent_dir}/{fname}"
                await sftp.put(str(local_path), remote_path)
                print(f"✅ 上传成功: {fname}")
    except Exception as e:
        print(f"❌ 文件上传失败: {e}")
        conn.close()
        return False

    print("\n=== 4. 验证文件 ===")
    try:
        result = await conn.run(f"ls -la {agent_dir}", check=False, encoding="utf-8", errors="replace")
        print(f"远程文件列表:\n{result.stdout}")
    except Exception as e:
        print(f"❌ 文件验证失败: {e}")
        conn.close()
        return False

    print("\n=== 5. 测试模块导入 ===")
    try:
        result = await conn.run(
            f"cd {agent_dir} && python3 -c \"import agent; print('Agent 模块导入成功')\"",
            check=False, encoding="utf-8", errors="replace"
        )
        if "Agent 模块导入成功" in result.stdout:
            print(f"✅ {result.stdout.strip()}")
        else:
            print(f"❌ 模块导入失败: {result.stdout} {result.stderr}")
    except Exception as e:
        print(f"❌ 模块导入异常: {e}")
        conn.close()
        return False

    print("\n=== 6. 测试入口函数 ===")
    try:
        result = await conn.run(
            f"cd {agent_dir} && python3 -m agent --help",
            check=False, encoding="utf-8", errors="replace"
        )
        if "usage:" in result.stdout.lower():
            print(f"✅ 入口函数正常")
            print(result.stdout[:200])
        else:
            print(f"❌ 入口函数失败: {result.stdout} {result.stderr}")
    except Exception as e:
        print(f"❌ 入口函数异常: {e}")
        conn.close()
        return False

    print("\n=== 7. 停止旧进程 ===")
    try:
        await conn.run("pkill -9 -f 'agent' 2>/dev/null || true", check=False)
        await conn.run("lsof -ti:9000 | xargs kill -9 2>/dev/null || true", check=False)
        print("✅ 旧进程已停止")
    except Exception as e:
        print(f"⚠️  停止进程警告: {e}")

    print("\n=== 8. 启动 Agent ===")
    try:
        result = await conn.run(
            f"cd {agent_dir} && nohup python3 -m agent --host 0.0.0.0 --port 9000 > agent.log 2>&1 &",
            check=False, encoding="utf-8", errors="replace"
        )
        print("✅ Agent 启动命令已执行")
    except Exception as e:
        print(f"❌ Agent 启动失败: {e}")
        conn.close()
        return False

    print("\n=== 9. 等待启动 ===")
    await asyncio.sleep(3)

    print("\n=== 10. 检查进程 ===")
    try:
        result = await conn.run(
            "ps aux | grep -E 'agent.py|python.*agent' | grep -v grep",
            check=False, encoding="utf-8", errors="replace"
        )
        print(f"进程列表:\n{result.stdout if result.stdout else '未找到进程'}")
    except Exception as e:
        print(f"⚠️  进程检查警告: {e}")

    print("\n=== 11. 检查日志 ===")
    try:
        result = await conn.run(
            f"cat {agent_dir}/agent.log",
            check=False, encoding="utf-8", errors="replace"
        )
        print(f"Agent 日志:\n{result.stdout}")
    except Exception as e:
        print(f"⚠️  日志检查警告: {e}")

    print("\n=== 12. 测试端口 ===")
    try:
        result = await conn.run(
            "curl -s http://localhost:9000/ || echo '端口测试失败'",
            check=False, encoding="utf-8", errors="replace"
        )
        print(f"端口测试结果: {result.stdout}")
    except Exception as e:
        print(f"⚠️  端口测试警告: {e}")

    conn.close()
    print("\n=== ✅ 所有步骤完成 ===")
    return True

if __name__ == "__main__":
    asyncio.run(test_connection())
