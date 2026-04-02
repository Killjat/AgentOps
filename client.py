#!/usr/bin/env python3
"""AgentOps CLI 客户端"""
import argparse
import os
import sys
import requests
import yaml
from pathlib import Path
from tabulate import tabulate

BASE_URL = "http://localhost:8000"
_config = {"base_url": BASE_URL}
HOSTS_FILE = Path(__file__).parent / "hosts.yaml"


def _load_hosts() -> dict:
    """加载 hosts.yaml 中的服务器配置"""
    if not HOSTS_FILE.exists():
        return {}
    with open(HOSTS_FILE) as f:
        data = yaml.safe_load(f) or {}
    return data.get("hosts", {})


def _resolve_host(name_or_ip: str) -> dict:
    """
    解析目标主机：
    - 如果是 hosts.yaml 中的名称，返回对应配置
    - 否则当作 IP 直接使用（需要额外提供 --user/--password）
    """
    hosts = _load_hosts()
    if name_or_ip in hosts:
        return hosts[name_or_ip]
    return None


def _req(method, path, **kwargs):
    try:
        r = getattr(requests, method)(f"{_config['base_url']}{path}", **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        print(f"错误: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"连接失败: {e}")
        sys.exit(1)


# ── deploy ───────────────────────────────────────────────────

def cmd_deploy(args):
    """部署 Agent 到目标机器（支持 hosts.yaml 名称或直接 IP）"""
    cfg = _resolve_host(args.target)

    if cfg:
        # 从配置文件读取，命令行参数可覆盖
        payload = {
            "host":       cfg["host"],
            "port":       args.port or cfg.get("port", 22),
            "username":   args.user or cfg["username"],
            "deploy_dir": args.dir  or cfg.get("deploy_dir", "/opt/agentops"),
        }
        if args.password:
            payload["password"] = args.password
        elif cfg.get("password"):
            payload["password"] = cfg["password"]
        if args.key:
            payload["ssh_key"] = args.key
        elif cfg.get("ssh_key"):
            payload["ssh_key"] = os.path.expanduser(cfg["ssh_key"])
    else:
        # 直接用 IP，必须提供 --user
        if not args.user:
            print(f"错误: '{args.target}' 不在 hosts.yaml 中，请用 --user 指定用户名")
            sys.exit(1)
        payload = {
            "host":       args.target,
            "port":       args.port or 22,
            "username":   args.user,
            "deploy_dir": args.dir or "/opt/agentops",
        }
        if args.password:
            payload["password"] = args.password
        if args.key:
            payload["ssh_key"] = args.key

    print(f"正在部署到 {payload['username']}@{payload['host']}:{payload['port']} ...")
    info = _req("post", "/agents/deploy", json=payload)
    print(f"\n✅ 部署成功")
    print(f"   Agent ID : {info['agent_id']}")
    print(f"   系统     : {info['os_version']}")
    print(f"   目录     : {info['deploy_dir']}")
    print(f"   状态     : {info['status']}")


def cmd_hosts(args):
    """列出 hosts.yaml 中配置的所有服务器"""
    hosts = _load_hosts()
    if not hosts:
        print(f"hosts.yaml 中暂无配置，请编辑 {HOSTS_FILE}")
        return
    rows = []
    for name, cfg in hosts.items():
        auth = f"key:{cfg['ssh_key']}" if cfg.get("ssh_key") else "password"
        rows.append([name, cfg["host"], cfg.get("port", 22),
                     cfg["username"], auth, cfg.get("deploy_dir", "/opt/agentops")])
    print(tabulate(rows, headers=["名称", "主机", "端口", "用户", "认证", "部署目录"],
                   tablefmt="rounded_outline"))


# ── agents ───────────────────────────────────────────────────

def cmd_agents(args):
    agents = _req("get", "/agents")
    if not agents:
        print("暂无 Agent")
        return
    rows = [[a["agent_id"], a["host"], a["os_version"],
             a["status"], a.get("last_seen", "-")[:19]] for a in agents]
    print(tabulate(rows, headers=["ID", "主机", "系统", "状态", "最后在线"],
                   tablefmt="rounded_outline"))


def cmd_ping(args):
    result = _req("post", f"/agents/{args.agent_id}/ping")
    status = "✅ 在线" if result["online"] else "❌ 离线"
    print(f"{args.agent_id}: {status}")
    if result.get("info"):
        i = result["info"].get("info", {})
        print(f"   主机名: {i.get('hostname', '-')}")
        print(f"   系统  : {i.get('os', '-')} {i.get('os_version', '')[:40]}")


def cmd_remove(args):
    _req("delete", f"/agents/{args.agent_id}")
    print(f"Agent {args.agent_id} 已移除")


# ── tasks ────────────────────────────────────────────────────

def cmd_run(args):
    """下发自然语言任务"""
    payload = {
        "task": args.task,
        "agent_id": args.agent_id,
        "timeout": args.timeout,
        "auto_confirm": True,
    }
    task = _req("post", "/tasks", json=payload)
    task_id = task["task_id"]
    print(f"任务已提交: {task_id}")

    if args.wait:
        import time
        for _ in range(args.timeout + 10):
            time.sleep(1)
            t = _req("get", f"/tasks/{task_id}")
            if t["status"] not in ("pending", "running"):
                _print_task(t)
                return
        print("等待超时，使用 `task get <id>` 查看结果")


def cmd_task_get(args):
    t = _req("get", f"/tasks/{args.task_id}")
    _print_task(t)


def cmd_task_list(args):
    params = {}
    if args.agent_id:
        params["agent_id"] = args.agent_id
    tasks = _req("get", "/tasks", params=params)
    if not tasks:
        print("暂无任务")
        return
    rows = [[t["task_id"][:12], t["agent_id"], t["status"],
             t["task"][:40], (t.get("completed_at") or "-")[:19]] for t in tasks]
    print(tabulate(rows, headers=["ID", "Agent", "状态", "任务", "完成时间"],
                   tablefmt="rounded_outline"))


def _print_task(t):
    status_icon = {"success": "✅", "failed": "❌", "running": "⏳", "pending": "⏸"}.get(t["status"], "?")
    print(f"\n{status_icon} 任务: {t['task']}")
    print(f"   ID     : {t['task_id']}")
    print(f"   Agent  : {t['agent_id']}")
    print(f"   命令   : {t.get('command', '-')}")
    if t.get("output"):
        print(f"\n── 输出 ──────────────────────────────")
        print(t["output"].strip())
    if t.get("analysis"):
        print(f"\n── AI 分析 ───────────────────────────")
        print(t["analysis"].strip())
    if t.get("error"):
        print(f"\n── 错误 ──────────────────────────────")
        print(t["error"])


# ── main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="agentops")
    parser.add_argument("--server", default="http://localhost:8000", help="服务端地址")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # deploy
    p = sub.add_parser("deploy", help="部署 Agent 到目标机器")
    p.add_argument("target", help="hosts.yaml 中的服务器名称，或直接填 IP")
    p.add_argument("--user", default=None, help="用户名（配置文件中已有则可省略）")
    p.add_argument("--password", default=None)
    p.add_argument("--key", default=None, help="SSH 私钥路径")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--dir", default=None, help="部署目录")
    p.set_defaults(func=cmd_deploy)

    # hosts
    p = sub.add_parser("hosts", help="列出 hosts.yaml 中配置的服务器")
    p.set_defaults(func=cmd_hosts)

    # agents
    p = sub.add_parser("agents", help="列出所有 Agent")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("ping", help="检查 Agent 是否在线")
    p.add_argument("agent_id")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("remove", help="移除 Agent")
    p.add_argument("agent_id")
    p.set_defaults(func=cmd_remove)

    # run
    p = sub.add_parser("run", help="下发自然语言任务")
    p.add_argument("task", help="任务描述（自然语言）")
    p.add_argument("--agent", dest="agent_id", required=True)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--wait", action="store_true", default=True, help="等待结果")
    p.set_defaults(func=cmd_run)

    # task
    p = sub.add_parser("task", help="查看任务")
    tsub = p.add_subparsers(dest="tcmd", required=True)

    tp = tsub.add_parser("get")
    tp.add_argument("task_id")
    tp.set_defaults(func=cmd_task_get)

    tp = tsub.add_parser("list")
    tp.add_argument("--agent", dest="agent_id", default=None)
    tp.set_defaults(func=cmd_task_list)

    args = parser.parse_args()
    _config["base_url"] = args.server.rstrip("/")
    args.func(args)


if __name__ == "__main__":
    main()
