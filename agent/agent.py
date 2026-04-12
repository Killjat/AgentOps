#!/usr/bin/env python3
"""
CyberAgentOps Agent 入口
支持直接运行：python3 agent.py
也支持模块运行：python3 -m agent
"""
import argparse
import platform
import sys
import os

# 确保当前目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="CyberAgentOps Agent")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--server", default="")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--type", default="auto",
                        choices=["auto", "linux", "windows", "mac", "android", "mobile"])
    args = parser.parse_args()

    agent_type = args.type

    # 从环境变量和 agent.conf 读取配置
    import pathlib
    exe_dir = pathlib.Path(sys.executable).parent if getattr(sys, 'frozen', False) else pathlib.Path(__file__).parent
    conf_server = ""
    conf_path = exe_dir / "agent.conf"
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("SERVER_URL=") and not args.server:
                conf_server = line.split("=", 1)[1].strip()
            elif line.startswith("AGENT_TOKEN="):
                os.environ.setdefault("AGENT_TOKEN", line.split("=", 1)[1].strip())

    server_url = args.server or os.getenv("SERVER_URL", "") or conf_server or "https://47.111.28.162:8443"
    if agent_type == "auto":
        os_name = platform.system().lower()
        if "windows" in os_name:
            agent_type = "windows"
        elif "darwin" in os_name:
            agent_type = "mac"
        elif "android" in os_name or "ANDROID_ROOT" in __import__('os').environ:
            agent_type = "android"
        else:
            agent_type = "linux"

    if agent_type == "windows":
        from windows import WindowsAgent
        agent = WindowsAgent(
            agent_id=args.agent_id,
            server_url=server_url,
            port=args.port,
            host=args.host,
        )
    elif agent_type == "mac":
        from mac import MacAgent
        agent = MacAgent(
            agent_id=args.agent_id,
            server_url=server_url,
            port=args.port,
            host=args.host,
        )
    elif agent_type in ("android", "mobile"):
        from android import AndroidAgent
        agent = AndroidAgent(
            agent_id=args.agent_id,
            server_url=server_url,
            port=args.port,
            host=args.host,
        )
    else:
        from linux import LinuxAgent
        agent = LinuxAgent(
            agent_id=args.agent_id,
            server_url=server_url,
            port=args.port,
            host=args.host,
        )

    agent.start()


if __name__ == "__main__":
    main()
