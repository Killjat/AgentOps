"""持久化函数"""
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path

from models import ServerInfo, OSType

logger = logging.getLogger(__name__)

SERVERS_FILE = Path(__file__).parent.parent.parent / "servers.yaml"
AGENTS_FILE = Path(__file__).parent.parent.parent / "agents.json"
TASKS_FILE = Path(__file__).parent.parent.parent / "tasks.json"
APP_DEPLOYS_FILE = Path(__file__).parent.parent.parent / "app_deploys.json"
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"


def _load_json(file_path: Path, default: dict) -> dict:
    """加载 JSON 文件，不存在则返回默认值"""
    if not file_path.exists():
        return default
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[警告] 加载 {file_path} 失败: {e}")
        return default


def _save_json(file_path: Path, data: dict):
    """保存数据到 JSON 文件"""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"[警告] 保存 {file_path} 失败: {e}")


def _load_servers_yaml() -> dict:
    """加载 servers.yaml"""
    if not SERVERS_FILE.exists():
        return {}
    with open(SERVERS_FILE) as f:
        return (yaml.safe_load(f) or {}).get("servers", {})


def _save_servers_yaml():
    """保存 servers.yaml"""
    from core.state import servers
    SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "servers": {
            k: {
                "name": v.name,
                "host": v.host,
                "port": v.port,
                "username": v.username,
                "password": v.password,
                "ssh_key": v.ssh_key,
                "os_type": v.os_type.value if isinstance(v.os_type, OSType) else v.os_type,
                "os_version": v.os_version,
                "owner": v.owner,
                "created_at": v.created_at,
                "last_connected": v.last_connected
            }
            for k, v in servers.items()
        }
    }
    with open(SERVERS_FILE, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _save_agents():
    """保存 agents 到文件"""
    from core.state import agents
    data = {k: v.model_dump(mode='json', exclude_none=True) for k, v in agents.items()}
    _save_json(AGENTS_FILE, data)


def _save_tasks():
    """保存 tasks 到文件"""
    from core.state import tasks
    data = {k: v.model_dump(mode='json', exclude_none=True) for k, v in tasks.items()}
    _save_json(TASKS_FILE, data)


def _save_app_deploys():
    """保存 app_deploys 到文件"""
    from core.state import app_deploys
    data = {k: v.model_dump(mode='json', exclude_none=True) for k, v in app_deploys.items()}
    _save_json(APP_DEPLOYS_FILE, data)


def _append_deploy_log(deploy_id: str, message: str):
    """追加部署日志到单独的文件"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{deploy_id}.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"[警告] 写入部署日志失败: {e}")


def _load_persistent_data():
    """启动时加载持久化数据"""
    from core.state import servers, agents, tasks, app_deploys

    # 加载 servers
    logger.info(f"[加载] 从 {SERVERS_FILE} 加载 servers...")
    servers_data = _load_servers_yaml()
    for server_id, data in servers_data.items():
        try:
            servers[server_id] = ServerInfo(
                server_id=server_id,
                name=data.get("name", server_id),
                host=data["host"],
                port=data.get("port", 22),
                username=data["username"],
                password=data.get("password"),
                ssh_key=data.get("ssh_key"),
                os_type=OSType(data.get("os_type", "unknown")),
                os_version=data.get("os_version", ""),
                owner=data.get("owner", ""),
                created_at=data.get("created_at", ""),
                last_connected=data.get("last_connected")
            )
        except Exception as e:
            logger.error(f"[加载] 加载 server {server_id} 失败: {e}")
    logger.info(f"[加载] servers 加载完成，共 {len(servers)} 个")

    # 加载 agents
    logger.info(f"[加载] 从 {AGENTS_FILE} 加载 agents...")
    agents_data = _load_json(AGENTS_FILE, {})
    logger.info(f"[加载] agents 文件中有 {len(agents_data)} 条记录")
    for agent_id, data in agents_data.items():
        try:
            if "host" in data:
                logger.warning(f"[加载] Agent {agent_id} 使用旧数据格式，需要迁移")
            from models import AgentInfo
            agents[agent_id] = AgentInfo(**data)
            logger.info(f"[加载] 成功加载 agent: {agent_id}")
        except Exception as e:
            logger.error(f"[加载] 加载 agent {agent_id} 失败: {e}")
    logger.info(f"[加载] agents 加载完成，共 {len(agents)} 个")

    # 加载 tasks
    logger.info(f"[加载] 从 {TASKS_FILE} 加载 tasks...")
    tasks_data = _load_json(TASKS_FILE, {})
    logger.info(f"[加载] tasks 文件中有 {len(tasks_data)} 条记录")
    for task_id, data in tasks_data.items():
        try:
            from models import TaskResult
            tasks[task_id] = TaskResult(**data)
        except Exception as e:
            logger.error(f"[加载] 加载 task {task_id} 失败: {e}")
    logger.info(f"[加载] tasks 加载完成，共 {len(tasks)} 个")

    # 加载 app_deploys
    logger.info(f"[加载] 从 {APP_DEPLOYS_FILE} 加载 app_deploys...")
    deploys_data = _load_json(APP_DEPLOYS_FILE, {})
    logger.info(f"[加载] app_deploys 文件中有 {len(deploys_data)} 条记录")
    for deploy_id, data in deploys_data.items():
        try:
            if "agent_id" in data and "target_type" not in data:
                data["target_type"] = "agent"
                data["target_id"] = data["agent_id"]
            from models import AppDeployResult
            app_deploys[deploy_id] = AppDeployResult(**data)
            logger.info(f"[加载] 成功加载 deploy: {deploy_id}")
        except Exception as e:
            logger.error(f"[加载] 加载 deploy {deploy_id} 失败: {e}")
    logger.info(f"[加载] app_deploys 加载完成，共 {len(app_deploys)} 个")

    print(f"[持久化] 已加载: {len(servers)} 个服务器, {len(agents)} 个 Agent, {len(tasks)} 个任务, {len(app_deploys)} 个应用部署")
