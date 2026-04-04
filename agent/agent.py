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
            server_url=args.server,
            port=args.port,
            host=args.host,
        )
    elif agent_type == "mac":
        from mac import MacAgent
        agent = MacAgent(
            agent_id=args.agent_id,
            server_url=args.server,
            port=args.port,
            host=args.host,
        )
    elif agent_type in ("android", "mobile"):
        from android import AndroidAgent
        agent = AndroidAgent(
            agent_id=args.agent_id,
            server_url=args.server,
            port=args.port,
            host=args.host,
        )
    else:
        from linux import LinuxAgent
        agent = LinuxAgent(
            agent_id=args.agent_id,
            server_url=args.server,
            port=args.port,
            host=args.host,
        )

    agent.start()


if __name__ == "__main__":
    main()
