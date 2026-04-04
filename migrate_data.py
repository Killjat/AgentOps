#!/usr/bin/env python3
"""
数据迁移脚本：从旧架构迁移到新架构

旧架构：
- hosts.yaml: 存储 SSH 连接信息
- agents.json: 存储已部署的 Agent（包含完整的 SSH 连接信息）
- app_deploys.json: 存储应用部署（使用 agent_id）

新架构：
- servers.yaml: 存储 Server 连接信息
- agents.json: 存储 Agent（引用 server_id）
- app_deploys.json: 存储应用部署（支持 target_type/target_id）
"""

import json
import uuid
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# 文件路径
BASE_DIR = Path(__file__).parent
OLD_HOSTS_FILE = BASE_DIR / "hosts.yaml"
NEW_SERVERS_FILE = BASE_DIR / "servers.yaml"
AGENTS_FILE = BASE_DIR / "agents.json"
APP_DEPLOYS_FILE = BASE_DIR / "app_deploys.json"


def migrate_hosts_to_servers() -> Dict[str, Any]:
    """将 hosts.yaml 迁移到 servers.yaml"""
    print("=== 迁移 hosts.yaml → servers.yaml ===")

    if not OLD_HOSTS_FILE.exists():
        print(f"⚠️  {OLD_HOSTS_FILE} 不存在，跳过")
        return {}

    with open(OLD_HOSTS_FILE) as f:
        hosts_data = yaml.safe_load(f) or {}

    servers = {}
    for host_key, host_info in hosts_data.get("hosts", {}).items():
        # 生成 server_id
        server_id = f"server-{uuid.uuid4().hex[:8]}"

        servers[server_id] = {
            "name": host_info.get("name", host_key),
            "host": host_info["host"],
            "port": host_info.get("port", 22),
            "username": host_info["username"],
            "password": host_info.get("password"),
            "ssh_key": host_info.get("ssh_key"),
            "os_type": host_info.get("os_type", "unknown"),
            "os_version": "",
            "owner": host_info.get("owner", ""),
            "created_at": datetime.now().isoformat(),
            "last_connected": None
        }

        print(f"✅ 迁移 Host '{host_key}' → Server '{server_id}'")

    # 保存到 servers.yaml
    with open(NEW_SERVERS_FILE, "w") as f:
        yaml.dump({"servers": servers}, f, allow_unicode=True, default_flow_style=False)

    print(f"✅ 已保存 {len(servers)} 个服务器到 {NEW_SERVERS_FILE}")
    return servers


def migrate_agents(servers_map: Dict[str, Dict[str, Any]]):
    """将 agents.json 迁移到新格式（关联 server_id）"""
    print("\n=== 迁移 agents.json ===")

    if not AGENTS_FILE.exists():
        print(f"⚠️  {AGENTS_FILE} 不存在，跳过")
        return

    with open(AGENTS_FILE) as f:
        agents_data = json.load(f)

    # 建立 host → server_id 的映射
    host_to_server_id = {}
    for server_id, server_info in servers_map.items():
        # 通过 host 地址匹配
        host_to_server_id[server_info["host"]] = server_id

    updated_agents = {}
    for agent_id, agent_info in agents_data.items():
        # 检查是否已经是新格式（有 server_id）
        if "server_id" in agent_info:
            print(f"⏭️  Agent {agent_id} 已是新格式，跳过")
            updated_agents[agent_id] = agent_info
            continue

        # 旧格式：需要迁移
        host = agent_info.get("host")
        if not host:
            print(f"⚠️  Agent {agent_id} 缺少 host 信息，无法迁移")
            continue

        # 查找对应的 server_id
        server_id = host_to_server_id.get(host)
        if not server_id:
            print(f"⚠️  Agent {agent_id} 的 host '{host}' 未找到对应的服务器")
            continue

        # 转换为新格式
        new_agent_info = {
            "agent_id": agent_id,
            "server_id": server_id,
            "name": agent_info.get("name", ""),
            "owner": agent_info.get("owner", ""),
            "os_type": agent_info.get("os_type", "unknown"),
            "os_version": agent_info.get("os_version", ""),
            "device_type": agent_info.get("device_type", "server"),
            "connection_type": agent_info.get("connection_type", "ssh"),
            "agent_deploy_dir": agent_info.get("deploy_dir", "/opt/agentops"),
            "agent_port": agent_info.get("agent_port", 9000),
            "status": agent_info.get("status", "offline"),
            "created_at": agent_info.get("created_at", datetime.now().isoformat()),
            "last_seen": agent_info.get("last_seen"),
            "metrics": agent_info.get("metrics")
        }

        updated_agents[agent_id] = new_agent_info
        print(f"✅ 迁移 Agent {agent_id} → Server {server_id}")

    # 保存回 agents.json
    with open(AGENTS_FILE, "w") as f:
        json.dump(updated_agents, f, ensure_ascii=False, indent=2)

    print(f"✅ 已迁移 {len(updated_agents)} 个 Agent 到 {AGENTS_FILE}")


def migrate_app_deploys():
    """迁移 app_deploys.json（添加 target_type/target_id）"""
    print("\n=== 迁移 app_deploys.json ===")

    if not APP_DEPLOYS_FILE.exists():
        print(f"⚠️  {APP_DEPLOYS_FILE} 不存在，跳过")
        return

    with open(APP_DEPLOYS_FILE) as f:
        deploys_data = json.load(f)

    updated_deploys = {}
    for deploy_id, deploy_info in deploys_data.items():
        # 检查是否已经是新格式
        if "target_type" in deploy_info:
            updated_deploys[deploy_id] = deploy_info
            continue

        # 旧格式：有 agent_id，但没有 target_type/target_id
        if "agent_id" in deploy_info:
            deploy_info["target_type"] = "agent"
            deploy_info["target_id"] = deploy_info["agent_id"]

            # 将 deploy_dir 改为 app_deploy_dir
            if "deploy_dir" in deploy_info:
                deploy_info["app_deploy_dir"] = deploy_info.pop("deploy_dir")

            updated_deploys[deploy_id] = deploy_info
            print(f"✅ 迁移部署 {deploy_id}")
        else:
            print(f"⚠️  部署 {deploy_id} 缺少 agent_id，跳过")

    # 保存回 app_deploys.json
    with open(APP_DEPLOYS_FILE, "w") as f:
        json.dump(updated_deploys, f, ensure_ascii=False, indent=2)

    print(f"✅ 已迁移 {len(updated_deploys)} 个部署记录到 {APP_DEPLOYS_FILE}")


def main():
    print("=" * 60)
    print("数据迁移：旧架构 → 新架构")
    print("=" * 60)

    # 1. 迁移 hosts → servers
    servers_map = migrate_hosts_to_servers()

    # 2. 迁移 agents
    if servers_map:
        migrate_agents(servers_map)

    # 3. 迁移 app_deploys
    migrate_app_deploys()

    print("\n" + "=" * 60)
    print("✅ 数据迁移完成！")
    print("=" * 60)
    print("\n后续步骤：")
    print("1. 重启服务器使新代码生效")
    print("2. 检查前端 API 调用是否需要更新")
    print("3. 旧文件 hosts.yaml 可以备份后删除")


if __name__ == "__main__":
    main()
