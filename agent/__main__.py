#!/usr/bin/env python3
"""
CyberAgentOps Agent 入口
根据当前操作系统自动选择对应的 Agent 实现
"""
import argparse
import platform
import sys


def main():
    parser = argparse.ArgumentParser(description="CyberAgentOps Agent")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--server", default="", help="控制端地址，如 http://1.2.3.4:8000")
    parser.add_argument("--agent-id", default="", help="Agent ID")
    parser.add_argument("--type", default="auto",
                        choices=["auto", "linux", "windows", "mobile"],
                        help="Agent 类型，默认自动检测")
    args = parser.parse_args()

    # 自动检测或手动指定 Agent 类型
    agent_type = args.type
    if agent_type == "auto":
        os_name = platform.system().lower()
        if "windows" in os_name:
            agent_type = "windows"
        elif "darwin" in os_name or "linux" in os_name:
            agent_type = "linux"
        else:
            agent_type = "linux"  # 默认 Linux

    # 加载对应 Agent
    if agent_type == "windows":
        from .windows import WindowsAgent
        agent = WindowsAgent(
            agent_id=args.agent_id,
            server_url=args.server,
            port=args.port,
            host=args.host,
        )
    elif agent_type == "linux":
        from .linux import LinuxAgent
        agent = LinuxAgent(
            agent_id=args.agent_id,
            server_url=args.server,
            port=args.port,
            host=args.host,
        )
    else:
        print(f"不支持的 Agent 类型: {agent_type}", file=sys.stderr)
        sys.exit(1)

    agent.start()


if __name__ == "__main__":
    main()
