"""AgentOps 控制端服务器"""
import uuid
import aiohttp
import os
import asyncio
import yaml
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 自动加载 .env
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()  # 强制覆盖

import asyncssh
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import sys
sys.path.insert(0, str(Path(__file__).parent))

from models import (
    AgentInfo, AgentStatus, TaskRequest, TaskResult, TaskStatus,
    ChatRequest, AppDeployRequest, AppDeployResult, AppDeployStatus, OSType,
    ServerInfo, RemoteHost, DeviceType, ConnectionType
)
from deployer import deploy, undeploy, update
import llm as LLM

SERVERS_FILE = Path(__file__).parent.parent / "servers.yaml"
WEB_DIR = Path(__file__).parent.parent / "web"
AGENTS_FILE = Path(__file__).parent.parent / "agents.json"
TASKS_FILE = Path(__file__).parent.parent / "tasks.json"
APP_DEPLOYS_FILE = Path(__file__).parent.parent / "app_deploys.json"
LOGS_DIR = Path(__file__).parent.parent / "logs"

# 内存存储（可替换为数据库）
servers: Dict[str, ServerInfo] = {}
agents: Dict[str, AgentInfo] = {}
tasks: Dict[str, TaskResult] = {}
app_deploys: Dict[str, AppDeployResult] = {}

# ── 持久化函数 ────────────────────────────────────────────────────
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

def _load_persistent_data():
    """启动时加载持久化数据"""
    global servers, agents, tasks, app_deploys

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
            # 兼容旧数据格式：如果有 host/port 等字段，需要迁移
            if "host" in data:
                # 旧数据格式，标记为待迁移
                logger.warning(f"[加载] Agent {agent_id} 使用旧数据格式，需要迁移")
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
            # 兼容旧数据：如果有 agent_id 但没有 target_type/target_id
            if "agent_id" in data and "target_type" not in data:
                data["target_type"] = "agent"
                data["target_id"] = data["agent_id"]
            app_deploys[deploy_id] = AppDeployResult(**data)
            logger.info(f"[加载] 成功加载 deploy: {deploy_id}")
        except Exception as e:
            logger.error(f"[加载] 加载 deploy {deploy_id} 失败: {e}")
    logger.info(f"[加载] app_deploys 加载完成，共 {len(app_deploys)} 个")

    print(f"[持久化] 已加载: {len(servers)} 个服务器, {len(agents)} 个 Agent, {len(tasks)} 个任务, {len(app_deploys)} 个应用部署")

def _load_servers_yaml() -> dict:
    """加载 servers.yaml"""
    if not SERVERS_FILE.exists():
        return {}
    with open(SERVERS_FILE) as f:
        return (yaml.safe_load(f) or {}).get("servers", {})


def _save_servers_yaml():
    """保存 servers.yaml"""
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
    data = {k: v.model_dump(mode='json', exclude_none=True) for k, v in agents.items()}
    _save_json(AGENTS_FILE, data)

def _save_tasks():
    """保存 tasks 到文件"""
    data = {k: v.model_dump(mode='json', exclude_none=True) for k, v in tasks.items()}
    _save_json(TASKS_FILE, data)

def _save_app_deploys():
    """保存 app_deploys 到文件"""
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

# ── 生命周期事件 ────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时加载数据
    logger.info("[lifespan] 开始加载持久化数据...")
    try:
        _load_persistent_data()
        logger.info("[lifespan] 数据加载完成")
    except Exception as e:
        logger.error(f"[lifespan] 数据加载失败: {e}")
        import traceback
        traceback.print_exc()

    yield

    # 关闭时保存数据
    logger.info("[lifespan] 开始保存数据...")
    try:
        _save_agents()
        _save_tasks()
        _save_app_deploys()
        logger.info("[持久化] 数据已保存")
    except Exception as e:
        logger.error(f"[lifespan] 数据保存失败: {e}")
        import traceback
        traceback.print_exc()

app = FastAPI(title="CyberAgentOps", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── WebSocket 连接池 ─────────────────────────────────────────
from fastapi import WebSocket, WebSocketDisconnect

# agent_id → WebSocket 连接
_ws_connections: Dict[str, WebSocket] = {}
# agent_id → 待处理任务 {task_id → asyncio.Future}
_ws_pending: Dict[str, Dict[str, asyncio.Future]] = {}


@app.websocket("/ws/agent/{agent_id}")
async def ws_agent_endpoint(websocket: WebSocket, agent_id: str):
    await websocket.accept()
    _ws_connections[agent_id] = websocket
    _ws_pending.setdefault(agent_id, {})
    print(f"[WS] Agent {agent_id} 已连接")

    # 更新 agent 状态为 online
    if agent_id in agents:
        agents[agent_id].status = AgentStatus.ONLINE
        agents[agent_id].last_seen = datetime.now().isoformat()

    try:
        async for raw in websocket.iter_text():
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type")
                task_id = msg.get("task_id", "")

                if msg_type == "register":
                    os_info = msg.get("os_info", {})
                    if agent_id in agents:
                        # 已知 agent，更新状态
                        agents[agent_id].status = AgentStatus.ONLINE
                        agents[agent_id].last_seen = datetime.now().isoformat()
                    else:
                        # 未知 agent，自动注册（主动接入场景：Mac/PC 用户自己运行 agent）
                        os_name = os_info.get("os", "").lower()
                        if "windows" in os_name:
                            os_type = OSType.WINDOWS
                        elif "darwin" in os_name:
                            os_type = OSType.MACOS
                        elif "android" in os_name:
                            os_type = OSType.ANDROID
                        else:
                            os_type = OSType.LINUX
                        device_type = DeviceType.MOBILE_ANDROID if os_type == OSType.ANDROID else DeviceType.DESKTOP
                        new_agent = AgentInfo(
                            agent_id=agent_id,
                            server_id="",
                            name=os_info.get("hostname", agent_id),
                            owner="admin",
                            os_type=os_type,
                            os_version=os_info.get("os_version", ""),
                            device_type=device_type,
                            connection_type=ConnectionType.AGENT_PUSH,
                            agent_deploy_dir="",
                            status=AgentStatus.ONLINE,
                            created_at=datetime.now().isoformat(),
                            last_seen=datetime.now().isoformat(),
                        )
                        agents[agent_id] = new_agent
                        _save_agents()
                        print(f"[WS] 新 Agent 自注册: {agent_id} ({os_info.get('hostname', '')})")
                    print(f"[WS] Agent {agent_id} 注册: {os_info.get('os', '')}")
                    # 连接后立即采集一次指标
                    asyncio.create_task(_collect_metrics_now(agent_id))

                elif msg_type == "pong":
                    if agent_id in agents:
                        agents[agent_id].last_seen = datetime.now().isoformat()

                elif task_id and task_id in _ws_pending.get(agent_id, {}):
                    # 任务响应，resolve 对应的 Future
                    fut = _ws_pending[agent_id].pop(task_id)
                    if not fut.done():
                        fut.set_result(msg)

            except Exception as e:
                print(f"[WS] 消息处理错误: {e}")

    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.pop(agent_id, None)
        # 断线时取消所有等待中的 Future，避免泄漏
        pending = _ws_pending.pop(agent_id, {})
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(f"Agent {agent_id} 断线"))
        if agent_id in agents:
            agents[agent_id].status = AgentStatus.OFFLINE
        print(f"[WS] Agent {agent_id} 断开连接，取消 {len(pending)} 个待处理任务")


async def _ws_call(agent_id: str, msg: dict, timeout: int = 60) -> dict:
    """通过 WebSocket 向 agent 发送消息并等待响应"""
    ws = _ws_connections.get(agent_id)
    if not ws:
        raise HTTPException(status_code=503, detail="Agent 未连接（离线）")

    task_id = msg.get("task_id") or uuid.uuid4().hex[:8]
    msg["task_id"] = task_id

    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _ws_pending.setdefault(agent_id, {})[task_id] = fut

    await ws.send_text(json.dumps(msg))

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _ws_pending.get(agent_id, {}).pop(task_id, None)
        raise HTTPException(status_code=504, detail=f"Agent 响应超时（{timeout}s）")
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))



# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查端点，用于验证服务是否正常运行"""
    return {"status": "ok", "service": "CyberAgentOps"}

# 挂载 Web 界面
if WEB_DIR.exists():
    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False)
    async def serve_index():
        content = (WEB_DIR / "index.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    # 挂载静态资源子目录
    static_dir = WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── 用户与权限系统 ────────────────────────────────────────────
import secrets, hashlib

USERS_FILE = Path(__file__).parent.parent / "users.json"

def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text())

def _save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2))

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _guest_id(ip: str) -> str:
    """根据 IP 生成唯一游客 ID"""
    return "guest-" + hashlib.md5(ip.encode()).hexdigest()[:8]

# token -> {username, role}
# role: admin | user | guest
_sessions: dict = {}

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

class GrantRequest(BaseModel):
    username: str
    perms: List[str]

@app.post("/auth/guest")
async def guest_login(request: Request):
    """游客登录，按 IP 生成唯一 ID"""
    ip = request.client.host
    guest_id = _guest_id(ip)
    token = secrets.token_hex(32)
    _sessions[token] = {"username": guest_id, "role": "guest"}
    return {"token": token, "username": guest_id, "role": "guest", "perms": []}

@app.post("/auth/register")
async def register(req: RegisterRequest):
    if len(req.username) < 2 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="用户名至少2位，密码至少4位")
    users = _load_users()
    if req.username in users:
        raise HTTPException(status_code=400, detail="用户名已存在")
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    if req.username == admin_user:
        raise HTTPException(status_code=400, detail="不能使用该用户名")
    users[req.username] = {"password": _hash_pw(req.password), "perms": []}
    _save_users(users)
    return {"ok": True, "message": "注册成功"}

@app.post("/auth/login")
async def login(req: LoginRequest):
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "admin123")
    if req.username == admin_user and req.password == admin_pass:
        token = secrets.token_hex(32)
        _sessions[token] = {"username": req.username, "role": "admin"}
        return {"token": token, "username": req.username, "role": "admin", "perms": ["task", "host"]}
    # 普通用户登录
    users = _load_users()
    u = users.get(req.username)
    if u and u["password"] == _hash_pw(req.password):
        token = secrets.token_hex(32)
        _sessions[token] = {"username": req.username, "role": "user"}
        return {"token": token, "username": req.username, "role": "user", "perms": ["task", "host"]}
    raise HTTPException(status_code=401, detail="用户名或密码错误")

@app.post("/auth/logout")
async def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "")
    _sessions.pop(token, None)
    return {"ok": True}

@app.get("/auth/users")
async def list_users(authorization: str = Header(default="")):
    _check_perm(authorization, "admin")
    users = _load_users()
    return [{"username": k} for k in users.keys()]

def _get_session(authorization: str) -> Optional[dict]:
    token = authorization.replace("Bearer ", "")
    return _sessions.get(token)

def _get_caller(authorization: str) -> str:
    """获取当前用户名（未登录返回空字符串）"""
    s = _get_session(authorization)
    return s["username"] if s else ""

def _is_admin(authorization: str) -> bool:
    s = _get_session(authorization)
    return s is not None and s.get("role") == "admin"

def _check_perm(authorization: str, perm: str):
    s = _get_session(authorization)
    if not s:
        raise HTTPException(status_code=401, detail="请先登录")
    if perm == "admin" and s["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")

def _check_owner(authorization: str, owner: str, resource: str = "资源"):
    """检查是否是 owner 或 admin"""
    s = _get_session(authorization)
    if not s:
        raise HTTPException(status_code=401, detail="请先登录")
    if s["role"] == "admin":
        return  # admin 全部放行
    if s["username"] != owner:
        raise HTTPException(status_code=403, detail=f"无权操作他人的{resource}")


# ── Servers 配置管理 ────────────────────────────────────────────

class ServerEntry(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    os_type: Optional[str] = None


@app.get("/servers")
async def list_servers(authorization: str = Header(default="")):
    """获取服务器列表：admin 看全部，其他用户只看自己的"""
    caller = _get_caller(authorization)
    if _is_admin(authorization):
        return [s.model_dump(exclude_none=True) for s in servers.values()]
    # 非 admin 只返回自己添加的
    return [s.model_dump(exclude_none=True) for s in servers.values() if s.owner == caller]


@app.post("/servers")
async def add_server(entry: ServerEntry, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)

    # 去重：同一 host + port + username 已存在则直接返回
    for existing in servers.values():
        if existing.host == entry.host and existing.port == entry.port and existing.username == entry.username:
            # 更新名称和密码
            existing.name = entry.name
            if entry.password:
                existing.password = entry.password
            if entry.ssh_key:
                existing.ssh_key = entry.ssh_key
            _save_servers_yaml()
            return {"message": "服务器已存在，已更新信息", "server_id": existing.server_id}

    server_id = f"server-{uuid.uuid4().hex[:8]}"
    server = ServerInfo(
        server_id=server_id,
        name=entry.name,
        host=entry.host,
        port=entry.port,
        username=entry.username,
        password=entry.password,
        ssh_key=entry.ssh_key,
        os_type=OSType(entry.os_type) if entry.os_type else OSType.UNKNOWN,
        owner=caller,
        created_at=datetime.now().isoformat()
    )
    servers[server_id] = server
    _save_servers_yaml()
    return {"message": "添加成功", "server_id": server_id}


@app.put("/servers/{server_id}")
async def update_server(server_id: str, entry: ServerEntry, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    if server_id not in servers:
        raise HTTPException(status_code=404, detail="服务器不存在")

    # 只有 owner 或 admin 可以修改
    server = servers[server_id]
    if not _is_admin(authorization) and server.owner != _get_caller(authorization):
        raise HTTPException(status_code=403, detail="无权修改他人的服务器")

    server.name = entry.name
    server.host = entry.host
    server.port = entry.port
    server.username = entry.username
    server.password = entry.password
    server.ssh_key = entry.ssh_key
    if entry.os_type:
        server.os_type = OSType(entry.os_type)

    _save_servers_yaml()
    return {"message": "更新成功"}


@app.delete("/servers/{server_id}")
async def delete_server(server_id: str, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    if server_id not in servers:
        raise HTTPException(status_code=404, detail="服务器不存在")

    server = servers[server_id]
    if not _is_admin(authorization) and server.owner != _get_caller(authorization):
        raise HTTPException(status_code=403, detail="无权删除他人的服务器")

    # 检查是否有 Agent 依赖此服务器
    for agent in agents.values():
        if agent.server_id == server_id:
            raise HTTPException(status_code=400, detail=f"此服务器有 {len([a for a in agents.values() if a.server_id == server_id])} 个 Agent 依赖，请先删除 Agent")

    del servers[server_id]
    _save_servers_yaml()
    return {"message": "删除成功"}


@app.post("/servers/test")
async def test_server(entry: ServerEntry):
    """测试 SSH 连接是否可用"""
    import asyncssh
    kwargs = dict(host=entry.host, port=entry.port, username=entry.username,
                  known_hosts=None, preferred_auth="password,keyboard-interactive")
    if entry.password:
        kwargs["password"] = entry.password
    if entry.ssh_key:
        kwargs["client_keys"] = [entry.ssh_key]
    try:
        conn = await asyncssh.connect(**kwargs)
        r = await conn.run("uname -sr || ver", check=False)
        conn.close()
        return {"ok": True, "info": r.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Agent 部署管理 ────────────────────────────────────────────────

class AgentDeployRequest(BaseModel):
    server_id: str  # 目标服务器
    name: str = ""  # Agent 名称


# 存储 agent 部署任务日志 {deploy_id: {"log": str, "status": str, "agent_id": str}}
_agent_deploy_tasks: dict = {}


@app.post("/agents/deploy")
async def deploy_agent(req: AgentDeployRequest, background_tasks: BackgroundTasks,
                       authorization: str = Header(default="")):
    """创建 Agent 部署任务，立即返回 deploy_id，后台异步执行"""
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)

    if req.server_id not in servers:
        raise HTTPException(status_code=404, detail="服务器不存在")

    server = servers[req.server_id]
    if not _is_admin(authorization) and server.owner != caller:
        raise HTTPException(status_code=403, detail="无权在他人服务器上部署 Agent")

    deploy_id = uuid.uuid4().hex[:12]
    _agent_deploy_tasks[deploy_id] = {"log": "", "status": "running", "agent_id": None}

    background_tasks.add_task(_run_agent_deploy, deploy_id, req, server, caller)
    return {"deploy_id": deploy_id, "status": "running"}


async def _run_agent_deploy(deploy_id: str, req: AgentDeployRequest, server: ServerInfo, caller: str):
    task = _agent_deploy_tasks[deploy_id]

    def log(msg: str):
        task["log"] += msg + "\n"

    try:
        host = RemoteHost(
            name=req.name or server.name,
            host=server.host,
            port=server.port,
            username=server.username,
            password=server.password,
            ssh_key=server.ssh_key,
            deploy_dir="/opt/agentops",
        )
        info = await deploy(host, log)
        info.server_id = req.server_id
        info.name = req.name or server.name
        info.owner = caller

        # 去重：同一 server 已有 agent 则覆盖，不新建
        existing_agent_id = next(
            (aid for aid, a in agents.items() if a.server_id == req.server_id), None
        )
        if existing_agent_id:
            log(f"ℹ️ 覆盖已有 Agent: {existing_agent_id} → {info.agent_id}")
            del agents[existing_agent_id]
            _ws_connections.pop(existing_agent_id, None)

        agents[info.agent_id] = info
        _save_agents()
        task["agent_id"] = info.agent_id
        task["status"] = "success" if info.status == AgentStatus.ONLINE else "warning"
        asyncio.create_task(_collect_metrics_now(info.agent_id))
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"❌ 部署失败: {e or type(e).__name__}\n{err}")
        task["status"] = "failed"


@app.get("/agents/deploy/{deploy_id}/stream")
async def stream_agent_deploy_log(deploy_id: str):
    """SSE 实时推送 Agent 部署日志"""
    if deploy_id not in _agent_deploy_tasks:
        raise HTTPException(status_code=404, detail="部署任务不存在")

    async def event_generator():
        last_len = 0
        for _ in range(300):  # 最多等 5 分钟
            task = _agent_deploy_tasks.get(deploy_id)
            if not task:
                break
            current_log = task["log"]
            if len(current_log) > last_len:
                for line in current_log[last_len:].splitlines():
                    yield f"data: {line}\n\n"
                last_len = len(current_log)
            if task["status"] != "running":
                yield f"data: __STATUS__{task['status']}\n\n"
                if task.get("agent_id"):
                    yield f"data: __AGENT_ID__{task['agent_id']}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/agents/{agent_id}")
async def remove_agent(agent_id: str, authorization: str = Header(default="")):
    info = _get_agent(agent_id)
    _check_owner(authorization, info.owner, "Agent")

    if info.server_id not in servers:
        raise HTTPException(status_code=404, detail="关联的服务器不存在")

    server = servers[info.server_id]
    host = RemoteHost(
        name=info.name,
        host=server.host,
        port=server.port,
        username=server.username,
        password=server.password,
        ssh_key=server.ssh_key,
        deploy_dir=info.agent_deploy_dir,
    )
    await undeploy(host)

    del agents[agent_id]
    _save_agents()
    return {"message": f"Agent {agent_id} 已移除"}


@app.post("/agents/{agent_id}/update")
async def update_agent(agent_id: str, authorization: str = Header(default="")):
    """更新 Agent 代码并重启服务"""
    info = _get_agent(agent_id)
    _check_owner(authorization, info.owner, "Agent")

    host = RemoteHost(
        name=info.name,
        host=servers[info.server_id].host,
        port=servers[info.server_id].port,
        username=servers[info.server_id].username,
        password=servers[info.server_id].password,
        ssh_key=servers[info.server_id].ssh_key,
        deploy_dir=info.agent_deploy_dir,
    )
    try:
        result = await update(host, agent_id)
        return {
            "message": f"✅ Agent '{info.name or agent_id}' 更新成功",
            "agent_id": agent_id,
            "status": result["status"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@app.get("/agents", response_model=List[AgentInfo])
async def list_agents(authorization: str = Header(default="")):
    """admin 看全部，其他用户只看自己的"""
    all_agents = list(agents.values())
    if _is_admin(authorization):
        return all_agents
    caller = _get_caller(authorization)
    return [a for a in all_agents if a.owner == caller]


@app.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: str):
    return _get_agent(agent_id)


@app.get("/agents/{agent_id}/ports")
async def get_agent_ports(agent_id: str, authorization: str = Header(default="")):
    """获取 agent 机器上已占用的端口"""
    _check_perm(authorization, "login")
    info = _get_agent(agent_id)
    try:
        result = await _ws_call(agent_id, {
            "type": "exec",
            "command": "ss -tlnp 2>/dev/null | awk 'NR>1{print $4}' | grep -oP ':\\K\\d+' | sort -n | uniq || netstat -tlnp 2>/dev/null | awk 'NR>2{print $4}' | grep -oP ':\\K\\d+' | sort -n | uniq || lsof -i -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | grep -oP ':\\K\\d+' | sort -n | uniq"
        }, timeout=10)
        ports = [int(p) for p in (result.get("output") or "").splitlines() if p.strip().isdigit()]
        return {"ports": sorted(set(ports))}
    except Exception:
        return {"ports": []}


@app.post("/agents/{agent_id}/metrics")
async def receive_metrics(agent_id: str, payload: dict):
    """接收 Agent 上报的系统指标"""
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    info = agents[agent_id]
    info.metrics = payload.get("metrics", payload)
    info.last_seen = datetime.now().isoformat()
    info.status = AgentStatus.ONLINE
    _save_agents()  # 持久化 Agent 状态
    return {"ok": True}


@app.get("/agents/{agent_id}/metrics")
async def get_metrics(agent_id: str):
    """获取 Agent 最新上报的指标"""
    info = _get_agent(agent_id)
    if not info.metrics:
        raise HTTPException(status_code=404, detail="暂无上报数据")
    return info.metrics


@app.post("/agents/{agent_id}/ping")
async def ping_agent(agent_id: str):
    """检查 Agent 是否在线"""
    info = _get_agent(agent_id)
    # agent_push 类型（Android/Mac 主动接入）直接检查 WebSocket 连接
    if info.connection_type == ConnectionType.AGENT_PUSH:
        online = agent_id in _ws_connections
        info.status = AgentStatus.ONLINE if online else AgentStatus.OFFLINE
        _save_agents()
        return {"online": online, "info": {"hostname": info.name, "os": info.os_type.value}}
    result = await _agent_get(info, "/ping")
    info.status = AgentStatus.ONLINE if result else AgentStatus.OFFLINE
    info.last_seen = datetime.now().isoformat()
    _save_agents()  # 持久化 Agent 状态
    return {"online": bool(result), "info": result}


# ── 任务下发 ─────────────────────────────────────────────────

@app.post("/tasks", response_model=TaskResult)
async def submit_task(request: TaskRequest, background_tasks: BackgroundTasks,
                      authorization: str = Header(default="")):
    """下发任务，需要登录，且只能对自己的 Agent 下发"""
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)
    info = _get_agent(request.agent_id)
    _check_owner(authorization, info.owner, "Agent")

    task_id = uuid.uuid4().hex[:12]
    task = TaskResult(
        task_id=task_id,
        agent_id=request.agent_id,
        owner=caller,
        status=TaskStatus.PENDING,
        task=request.task,
        created_at=datetime.now().isoformat(),
    )
    tasks[task_id] = task
    _save_tasks()  # 持久化
    background_tasks.add_task(_run_task, task_id, request)
    return task


@app.get("/tasks", response_model=List[TaskResult])
async def list_tasks(agent_id: Optional[str] = None,
                     authorization: str = Header(default="")):
    result = list(tasks.values())
    if not _is_admin(authorization):
        caller = _get_caller(authorization)
        result = [t for t in result if t.owner == caller]
    if agent_id:
        result = [t for t in result if t.agent_id == agent_id]
    return sorted(result, key=lambda t: t.created_at, reverse=True)


@app.get("/tasks/{task_id}", response_model=TaskResult)
async def get_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tasks[task_id]


@app.post("/tasks/{task_id}/chat")
async def chat_with_task(task_id: str, req: ChatRequest,
                         authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = tasks[task_id]
    _check_owner(authorization, task.owner, "任务")

    task = tasks[task_id]
    info = agents.get(task.agent_id)
    if not info:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    # 构建上下文
    context = f"""你是一个 Linux 运维专家，正在协助用户处理服务器问题。

原始任务：{task.task}
执行的命令：{task.command or '无'}
执行状态：{'成功' if task.status == TaskStatus.SUCCESS else '失败'}
命令输出：
{(task.output or task.error or '无输出')[:1500]}

之前的分析：{task.analysis or '无'}
"""
    if not task.conversation:
        task.conversation = []

    if req.execute:
        # 「问并执行」模式：让 AI 直接生成命令
        exec_system = context + "\n用户想执行操作，请只返回一条可直接执行的 shell 命令，不要任何解释，不要 markdown 格式。"
        cmd_messages = [{"role": "system", "content": exec_system}]
        for msg in task.conversation:
            if msg["role"] != "system":
                cmd_messages.append({"role": msg["role"], "content": msg["content"]})
        cmd_messages.append({"role": "user", "content": req.message})

        command = await LLM.chat(cmd_messages, max_tokens=200)
        command = command.strip().strip('`').strip()
        # 去掉可能的 bash/sh 前缀
        if command.startswith("bash\n") or command.startswith("sh\n"):
            command = command.split("\n", 1)[1].strip()

        # 执行命令
        exec_result = await _agent_exec(info, command, 60)
        success = exec_result.get("success", False)
        output = exec_result.get("output") or exec_result.get("error", "")

        # 让 AI 分析执行结果
        analysis_messages = [{"role": "system", "content": context}]
        for msg in task.conversation:
            if msg["role"] != "system":
                analysis_messages.append({"role": msg["role"], "content": msg["content"]})
        analysis_messages += [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": f"执行命令：`{command}`"},
            {"role": "user", "content": f"命令执行{'成功' if success else '失败'}，输出：\n{output[:1000]}\n\n请分析结果。"}
        ]
        reply = await LLM.chat(analysis_messages, max_tokens=500)

        task.conversation.append({"role": "user", "content": req.message})
        task.conversation.append({"role": "assistant", "content": f"执行命令：`{command}`\n\n{reply}"})

        return {
            "reply": reply,
            "command": command,
            "exec_result": exec_result,
            "conversation": task.conversation
        }
    else:
        # 普通对话模式：AI 分析回答，不执行
        messages = [{"role": "system", "content": context}]
        for msg in task.conversation:
            if msg["role"] != "system":
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": req.message})

        reply = await LLM.chat(messages, max_tokens=600)

        task.conversation.append({"role": "user", "content": req.message})
        task.conversation.append({"role": "assistant", "content": reply})

        return {
            "reply": reply,
            "command": None,
            "exec_result": None,
            "conversation": task.conversation
        }


# ── 内部逻辑 ─────────────────────────────────────────────────

def _get_agent(agent_id: str) -> AgentInfo:
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agents[agent_id]


def _ssh_kwargs(info: AgentInfo) -> dict:
    """构建 asyncssh 连接参数"""
    # 从 server_id 获取服务器信息
    server = servers.get(info.server_id)
    if not server:
        raise ValueError(f"服务器 {info.server_id} 不存在")
    
    kwargs = dict(host=server.host, port=server.port,
                  username=server.username, known_hosts=None,
                  keepalive_interval=15,
                  keepalive_count_max=6)
    if server.password:
        kwargs["password"] = server.password
        kwargs["preferred_auth"] = "password,keyboard-interactive"
    if server.ssh_key:
        kwargs["client_keys"] = [server.ssh_key]
    return kwargs


@asynccontextmanager
async def _ssh_tunnel(info: AgentInfo):
    """
    建立 SSH 本地端口转发隧道：
    本地随机端口 → 目标机器 localhost:agent_port
    用完自动关闭，目标机器无需对外开放任何端口。
    """
    # 找一个本地空闲端口
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]

    conn = await asyncssh.connect(**_ssh_kwargs(info))
    listener = await conn.forward_local_port(
        "127.0.0.1", local_port,
        "127.0.0.1", info.agent_port
    )
    try:
        yield local_port
    finally:
        listener.close()
        conn.close()


async def _agent_get(info: AgentInfo, path: str) -> Optional[dict]:
    """通过 SSH 检查 Agent 进程是否存活"""
    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            # 根据系统类型使用不同的命令检查进程
            if info.os_type == OSType.WINDOWS:
                # Windows: 使用 tasklist 检查 python 进程
                r = await conn.run('tasklist /FI "IMAGENAME eq pythonw.exe" /FI "STATUS eq running"', check=False,
                                   encoding="latin-1", errors="replace")
                running = "pythonw.exe" in (r.stdout or "")
                # 获取系统信息
                info_r = await conn.run("hostname", check=False, encoding="latin-1", errors="replace")
                hostname = (info_r.stdout or "").strip()
            else:
                # Linux/macOS: 使用 pgrep 检查
                r = await conn.run("pgrep -f 'agent.py' && echo running || echo stopped", check=False)
                running = "running" in (r.stdout or "")
                # 获取系统信息
                info_r = await conn.run("uname -n && uname -sr", check=False)
                lines = (info_r.stdout or "").strip().splitlines()
                hostname = lines[0] if lines else ""
                os_str = lines[1] if len(lines) > 1 else ""

            return {
                "pong": running,
                "info": {
                    "hostname": hostname,
                    "os": os_str if info.os_type != OSType.WINDOWS else "Windows",
                    "os_version": info.os_version,
                }
            }
        finally:
            conn.close()
    except Exception:
        return None


async def _agent_exec(info: AgentInfo, command: str, timeout: int) -> dict:
    """执行命令：优先走 WebSocket，降级走 SSH"""
    # 优先 WebSocket
    if info.agent_id in _ws_connections:
        try:
            resp = await _ws_call(info.agent_id, {"type": "exec", "command": command, "timeout": timeout}, timeout=timeout + 5)
            return {"success": resp.get("success", False), "output": resp.get("output", ""), "error": resp.get("error", "")}
        except HTTPException:
            pass  # 降级到 SSH

    # 降级：直接 SSH 执行
    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            result = await asyncio.wait_for(conn.run(command, check=False), timeout=timeout + 10)
            output = result.stdout or result.stderr or ""
            return {"success": result.exit_status == 0, "output": output, "error": ""}
        finally:
            conn.close()
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"命令执行超时（{timeout}s）"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


async def _collect_metrics_now(agent_id: str):
    """通过 WebSocket 采集一次系统指标"""
    await asyncio.sleep(3)
    if agent_id not in agents:
        return
    if agent_id not in _ws_connections:
        return
    try:
        resp = await _ws_call(agent_id, {"type": "metrics"}, timeout=30)
        metrics = resp.get("metrics", {})
        if metrics and agent_id in agents:
            agents[agent_id].metrics = metrics
            agents[agent_id].last_seen = datetime.now().isoformat()
            agents[agent_id].status = AgentStatus.ONLINE
            _save_agents()
    except Exception as e:
        print(f"[metrics] {agent_id} 采集失败: {e}")


# ── 应用部署 ─────────────────────────────────────────────────

from fastapi.responses import StreamingResponse

@app.post("/deploy/app/precheck")
async def precheck_deploy(request: AppDeployRequest,
                           authorization: str = Header(default="")):
    """部署前检查：端口占用、已有部署、依赖环境"""
    _check_perm(authorization, "login")
    info = _get_agent(request.agent_id)
    _check_owner(authorization, info.owner, "Agent")

    results = {}
    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            async def check(cmd):
                r = await conn.run(cmd, check=False)
                return (r.stdout or r.stderr or "").strip()

            # 1. 检查目录是否已有部署
            existing = await check(f"test -d {request.deploy_dir}/.git && git -C {request.deploy_dir} log -1 --oneline 2>/dev/null || echo 'not_exists'")
            if "not_exists" in existing:
                results["existing_deploy"] = {"status": "none", "message": "目录不存在，将进行首次部署"}
            else:
                results["existing_deploy"] = {"status": "found", "message": f"已有部署: {existing}"}

            # 2. 检查端口占用
            port = 8000
            port_check = await check(f"ss -tlnp | grep ':{port}' | head -3")
            if port_check:
                results["port"] = {"status": "occupied", "message": f"端口 {port} 已被占用: {port_check[:100]}"}
            else:
                results["port"] = {"status": "free", "message": f"端口 {port} 空闲"}

            # 3. 检查 Python 和 pip
            py_ver = await check("python3 --version 2>/dev/null || echo 'not_found'")
            pip_ver = await check("pip3 --version 2>/dev/null || python3 -m pip --version 2>/dev/null || echo 'not_found'")
            results["python"] = {
                "status": "ok" if "not_found" not in py_ver else "missing",
                "message": py_ver if "not_found" not in py_ver else "未安装 Python3"
            }
            results["pip"] = {
                "status": "ok" if "not_found" not in pip_ver else "missing",
                "message": pip_ver[:60] if "not_found" not in pip_ver else "未安装 pip，部署时会自动安装"
            }

            # 4. 检查 git
            git_ver = await check("git --version 2>/dev/null || echo 'not_found'")
            results["git"] = {
                "status": "ok" if "not_found" not in git_ver else "missing",
                "message": git_ver if "not_found" not in git_ver else "未安装 git，部署时会自动安装"
            }

            # 5. 检查磁盘空间
            disk = await check(f"df -h {request.deploy_dir} 2>/dev/null | tail -1 || df -h / | tail -1")
            results["disk"] = {"status": "ok", "message": disk}

            # 6. 检查 deploy.sh（如果是更新）
            if "found" in results.get("existing_deploy", {}).get("status", ""):
                has_deploy_sh = await check(f"test -f {request.deploy_dir}/deploy.sh && echo yes || echo no")
                results["deploy_sh"] = {
                    "status": "found" if "yes" in has_deploy_sh else "none",
                    "message": "仓库包含 deploy.sh，将直接执行" if "yes" in has_deploy_sh else "无 deploy.sh，将自动分析部署"
                }

        finally:
            conn.close()
    except Exception as e:
        results["error"] = {"status": "error", "message": str(e)}

    return results


@app.get("/deploy/app/{deploy_id}/stream")
async def stream_deploy_log(deploy_id: str, authorization: str = ""):
    """SSE 实时推送部署日志，支持 query string 传 token"""
    # EventSource 不支持自定义 header，从 query string 读
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    _check_perm(authorization.replace("Bearer ", "").strip(), "login") if authorization else None

    async def event_generator():
        last_len = 0
        for _ in range(300):  # 最多等 5 分钟
            if deploy_id in app_deploys:
                d = app_deploys[deploy_id]
                current_log = d.log or ""
                if len(current_log) > last_len:
                    new_content = current_log[last_len:]
                    for line in new_content.splitlines():
                        yield f"data: {line}\n\n"
                    last_len = len(current_log)
                if d.status in ("success", "failed"):
                    yield f"data: __STATUS__{d.status}\n\n"
                    break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/deploy/app", response_model=AppDeployResult)
async def create_app_deploy(request: AppDeployRequest, background_tasks: BackgroundTasks,
                             authorization: str = Header(default="")):
    """创建应用部署任务"""
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)
    info = _get_agent(request.agent_id)
    _check_owner(authorization, info.owner, "Agent")

    deploy_id = uuid.uuid4().hex[:12]
    result = AppDeployResult(
        deploy_id=deploy_id,
        agent_id=request.agent_id,
        owner=caller,
        repo_url=request.repo_url,
        deploy_dir=request.deploy_dir,
        status=AppDeployStatus.PENDING,
        created_at=datetime.now().isoformat(),
    )
    app_deploys[deploy_id] = result
    _save_app_deploys()  # 持久化
    background_tasks.add_task(_run_app_deploy, deploy_id, request)
    return result


@app.post("/deploy/app/{deploy_id}/upload")
async def upload_config_file(deploy_id: str,
                              file: UploadFile = File(...),
                              remote_path: str = "",
                              as_env: bool = False,
                              authorization: str = Header(default="")):
    """上传配置文件，as_env=true 时自动保存为 .env"""
    _check_perm(authorization, "login")
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")

    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")
    info = _get_agent(d.agent_id)

    content = await file.read()
    # as_env=true 强制保存为 .env，否则按 remote_path 或原文件名
    if as_env:
        target = f"{d.deploy_dir}/.env"
    else:
        target = remote_path or f"{d.deploy_dir}/{file.filename}"

    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            dir_path = "/".join(target.split("/")[:-1])
            await conn.run(f"mkdir -p {dir_path}", check=False)
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(target, 'wb') as f_remote:
                    await f_remote.write(content)
        finally:
            conn.close()
        return {"ok": True, "path": target, "size": len(content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/deploy/app/{deploy_id}", response_model=AppDeployResult)
async def get_app_deploy(deploy_id: str, authorization: str = Header(default="")):
    """获取单个部署详情"""
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")
    return d


@app.get("/deploy/app")
async def list_app_deploys(authorization: str = Header(default="")):
    result = list(app_deploys.values())
    if not _is_admin(authorization):
        caller = _get_caller(authorization)
        result = [d for d in result if d.owner == caller]
    return sorted(result, key=lambda d: d.created_at, reverse=True)


@app.post("/deploy/scan/{agent_id}")
async def scan_agent_apps(agent_id: str, authorization: str = Header(default="")):
    """扫描 Agent 服务器上的已部署应用"""
    _check_perm(authorization, "login")
    info = _get_agent(agent_id)

    try:
        # 优先走 WebSocket
        if agent_id in _ws_connections:
            resp = await _ws_call(agent_id, {"type": "discover"}, timeout=60)
            agent_data = resp.get("data", {})
        else:
            raise HTTPException(status_code=503, detail="Agent 未连接，请等待 Agent 上线后重试")

        discovered = []
        for svc in agent_data.get("services", []):
            if svc.get("port"):
                discovered.append({"type": "service", "name": svc["name"],
                                    "description": svc.get("description", ""),
                                    "port": svc["port"], "status": svc["status"]})
        for container in agent_data.get("containers", []):
            if container.get("port"):
                discovered.append({"type": "container", "name": container["name"],
                                    "description": f"Docker: {container.get('status', '')}",
                                    "port": container["port"], "status": "running"})
        for port_info in agent_data.get("ports", []):
            discovered.append({"type": "port", "name": f"Port {port_info['port']}",
                                "description": f"Process: {port_info.get('process', 'unknown')}",
                                "port": port_info["port"], "status": "listening"})

        return {"agent_id": agent_id, "hostname": agent_data.get("hostname", ""),
                "discovered": discovered, "count": len(discovered)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@app.post("/deploy/register/{agent_id}")
async def register_discovered_app(agent_id: str, req: dict, authorization: str = Header(default="")):
    """将发现的应用注册为技能"""
    _check_perm(authorization, "login")
    info = _get_agent(agent_id)

    name = req.get("name", "")
    app_type = req.get("type", "")
    port = req.get("port", "")
    description = req.get("description", "")

    if not name or not port:
        raise HTTPException(status_code=400, detail="name 和 port 是必填项")

    deploy_id = f"app-{uuid.uuid4().hex[:8]}"
    deploy_dir = f"/opt/{name.split('.')[0]}"  # 推断部署目录

    app_deploy = AppDeployResult(
        deploy_id=deploy_id,
        target_type="agent",
        target_id=agent_id,
        owner=_get_caller(authorization),
        repo_url=f"{app_type}://{name}",  # 标记为手动注册
        app_deploy_dir=deploy_dir,
        status=AppDeployStatus.SUCCESS,
        log=f"手动注册的 {app_type} 应用: {name}\n描述: {description}\n端口: {port}",
        created_at=datetime.now().isoformat()
    )

    app_deploys[deploy_id] = app_deploy
    _save_app_deploys()

    return app_deploy


@app.get("/deploy/app/{deploy_id}/log")
async def get_deploy_log(deploy_id: str, authorization: str = Header(default="")):
    """获取部署的详细日志文件"""
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")

    log_file = LOGS_DIR / f"{deploy_id}.log"
    if not log_file.exists():
        return {"log": d.log or "无详细日志", "file_exists": False}

    try:
        log_content = log_file.read_text(encoding='utf-8')
        return {"log": log_content, "file_exists": True}
    except Exception as e:
        return {"log": f"读取日志失败: {e}", "file_exists": False, "inline_log": d.log}


@app.post("/deploy/app/{deploy_id}/chat")
async def chat_with_deploy(deploy_id: str, req: ChatRequest,
                            authorization: str = Header(default="")):
    """基于部署结果继续对话，可执行命令或上传文件"""
    _check_perm(authorization, "login")
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")
    info = _get_agent(d.agent_id)

    if not hasattr(d, 'conversation'):
        d.__dict__['conversation'] = []
    conv = d.__dict__.get('conversation', [])
    context = f"""你是一个 Linux 运维和应用部署专家。
已部署的仓库：{d.repo_url}
部署目录：{d.deploy_dir}
目标服务器：{servers[info.server_id].host if info.server_id in servers else info.name} ({info.os_version})
部署状态：{d.status}
部署日志：
{d.log[-1000:] if d.log else '无'}
"""
    if req.execute:
        exec_system = context + "\n用户想执行操作，请只返回一条可直接执行的 shell 命令，不要任何解释，不要 markdown 格式。"
        cmd_messages = [{"role": "system", "content": exec_system}]
        for msg in conv:
            if msg["role"] != "system":
                cmd_messages.append({"role": msg["role"], "content": msg["content"]})
        cmd_messages.append({"role": "user", "content": req.message})

        command = await LLM.chat(cmd_messages, max_tokens=200)
        command = command.strip().strip('`').strip()
        if command.startswith("bash\n") or command.startswith("sh\n"):
            command = command.split("\n", 1)[1].strip()

        exec_result = await _agent_exec(info, command, 120)
        success = exec_result.get("success", False)
        output = exec_result.get("output") or exec_result.get("error", "")

        analysis_messages = [{"role": "system", "content": context}]
        for msg in conv:
            if msg["role"] != "system":
                analysis_messages.append({"role": msg["role"], "content": msg["content"]})
        analysis_messages += [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": f"执行命令：`{command}`"},
            {"role": "user", "content": f"命令执行{'成功' if success else '失败'}，输出：\n{output[:1000]}\n\n请分析结果。"}
        ]
        reply = await LLM.chat(analysis_messages, max_tokens=500)

        conv.append({"role": "user", "content": req.message})
        conv.append({"role": "assistant", "content": f"执行命令：`{command}`\n\n{reply}"})
        d.__dict__['conversation'] = conv
        _save_app_deploys()  # 持久化对话

        return {"reply": reply, "command": command, "exec_result": exec_result, "conversation": conv}
    else:
        messages = [{"role": "system", "content": context}]
        for msg in conv:
            if msg["role"] != "system":
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": req.message})
        reply = await LLM.chat(messages, max_tokens=600)
        conv.append({"role": "user", "content": req.message})
        conv.append({"role": "assistant", "content": reply})
        d.__dict__['conversation'] = conv
        _save_app_deploys()  # 持久化对话
        return {"reply": reply, "command": None, "exec_result": None, "conversation": conv}


async def _run_app_deploy(deploy_id: str, request: AppDeployRequest):
    """后台执行应用部署 - AI 智能分析 + 验证"""
    d = app_deploys[deploy_id]
    info = agents[request.agent_id]
    log_lines = []

    def log(msg: str):
        log_lines.append(msg)
        d.log = "\n".join(log_lines)
        print(f"[app-deploy] {msg}")

    try:
        d.status = AppDeployStatus.RUNNING
        conn = await asyncssh.connect(**_ssh_kwargs(info))

        try:
            async def run(cmd, timeout=120):
                r = await asyncio.wait_for(conn.run(cmd, check=False), timeout=timeout)
                out = (r.stdout or r.stderr or "").strip()
                if out:
                    log(out[-1000:])
                return r

            # ── 1. 安装 git ──────────────────────────────────────
            log(f"▶ 开始部署 {request.repo_url}")
            git_check = await conn.run("which git", check=False)
            if git_check.exit_status != 0:
                log("安装 git...")
                await run("apt-get install -y git 2>/dev/null || dnf install -y git 2>/dev/null || yum install -y git 2>/dev/null")

            # ── 2. clone / pull ───────────────────────────────────
            # 首先检查配置的目录是否已有该项目的部署
            check_dir = await conn.run(f"test -d {request.deploy_dir}/.git && echo exists", check=False)
            is_update = "exists" in (check_dir.stdout or "")

            if is_update:
                # 验证是否是同一个仓库
                git_remote = await conn.run(f"cd {request.deploy_dir} && git remote get-url origin 2>/dev/null", check=False)
                current_repo = (git_remote.stdout or "").strip()
                # 规范化仓库 URL（去掉 .git 后缀，统一协议）
                def normalize_repo_url(url):
                    url = url.rstrip('/')
                    if url.endswith('.git'):
                        url = url[:-4]
                    # 统一 https:// 和 git:// 协议
                    url = url.replace('git@github.com:', 'https://github.com/')
                    return url.lower()

                if normalize_repo_url(current_repo) != normalize_repo_url(request.repo_url):
                    log(f"⚠️  配置目录 {request.deploy_dir} 已有其他项目: {current_repo}")
                    log(f"▶ 新项目: {request.repo_url}")
                    log(f"▶ 将清空目录并重新 clone")
                    await run(f"rm -rf {request.deploy_dir}")
                    is_update = False

            if is_update:
                log(f"▶ 检测到已有部署，执行更新 (git pull {request.branch})")
                await run(f"cd {request.deploy_dir} && git fetch origin && git checkout {request.branch} && git pull origin {request.branch}")
            else:
                log(f"▶ 首次部署，git clone -> {request.deploy_dir}")
                await run(f"mkdir -p {request.deploy_dir}")
                await run(f"git clone -b {request.branch} {request.repo_url} {request.deploy_dir}", timeout=180)

            # ── 3. AI 分析仓库，生成部署计划 ─────────────────────
            log("▶ AI 分析仓库结构...")
            repo_info = await conn.run(
                f"ls -la {request.deploy_dir}/ && echo '===' && "
                f"cat {request.deploy_dir}/requirements.txt 2>/dev/null && echo '===' && "
                f"cat {request.deploy_dir}/package.json 2>/dev/null && echo '===' && "
                f"cat {request.deploy_dir}/Dockerfile 2>/dev/null && echo '===' && "
                f"cat {request.deploy_dir}/README.md 2>/dev/null | head -30",
                check=False
            )
            sys_info = await conn.run(
                "python3 --version 2>/dev/null; node --version 2>/dev/null; "
                "java -version 2>/dev/null; docker --version 2>/dev/null; "
                "which pip3 2>/dev/null; which npm 2>/dev/null",
                check=False
            )

            plan_prompt = f"""你是一个 DevOps 专家，分析以下仓库信息，生成部署计划。

仓库：{request.repo_url}
目标服务器系统：{info.os_version}
服务器已安装工具：
{(sys_info.stdout or '').strip()}

仓库文件结构和关键文件：
{(repo_info.stdout or '')[:3000]}

请生成一个 JSON 格式的部署计划：
{{
  "project_type": "python/node/java/docker/other",
  "description": "项目简介",
  "install_steps": ["步骤1命令", "步骤2命令"],
  "start_cmd": "启动命令",
  "health_check": "验证是否启动成功的命令",
  "expected_port": 8000,
  "warnings": ["注意事项1", "注意事项2"],
  "suggestions": ["建议1", "建议2"]
}}

只返回 JSON，不要其他内容。"""

            plan_raw = await LLM.chat([{"role": "user", "content": plan_prompt}], max_tokens=600)
            # 解析 AI 生成的部署计划
            import re as _re, json as _json
            plan = {}
            try:
                json_match = _re.search(r'\{.*\}', plan_raw, _re.DOTALL)
                if json_match:
                    plan = _json.loads(json_match.group())
            except Exception:
                pass

            log(f"\n── AI 部署分析 ──")
            log(f"项目类型: {plan.get('project_type', '未知')}")
            log(f"项目描述: {plan.get('description', '未知')}")
            if plan.get('warnings'):
                log(f"⚠️  注意: {'; '.join(plan.get('warnings', []))}")
            if plan.get('suggestions'):
                log(f"💡 建议: {'; '.join(plan.get('suggestions', []))}")
            log("─────────────────")

            # ── 4. 检查并关闭现有服务（包括禁用守护进程）────────────
            log("▶ 检测并关闭现有服务...")
            expected_port = plan.get('expected_port', 8000)

            # 步骤1: 查找并禁用相关的 systemd 服务
            log("  检查 systemd 服务...")
            svc_name = request.service_name or request.repo_url.split("/")[-1].replace(".", "")
            service_check = await conn.run(
                f"systemctl list-units --all | grep '{svc_name}' | awk '{{print $1}}'",
                check=False
            )

            if service_check.stdout and service_check.stdout.strip():
                services = [s.strip() for s in service_check.stdout.split('\n') if s.strip() and '.service' in s]
                for svc in services:
                    log(f"  ⚠️  发现 systemd 服务: {svc}")
                    # 停止服务
                    await run(f"systemctl stop {svc} 2>/dev/null || true", timeout=15)
                    # 禁用服务（防止开机自启）
                    await run(f"systemctl disable {svc} 2>/dev/null || true", timeout=10)
                    log(f"  ✅ 已停止并禁用服务: {svc}")
                await asyncio.sleep(3)

            # 步骤2: 通过端口查找并关闭进程
            port_check = await conn.run(
                f"lsof -ti:{expected_port} 2>/dev/null || ss -tlnp | grep ':{expected_port}' | awk '{{print $7}}' | cut -d, -f2 | cut -d= -f2",
                check=False
            )

            if port_check.stdout and port_check.stdout.strip():
                pids = port_check.stdout.strip().split()
                log(f"  ⚠️  发现端口 {expected_port} 被进程占用: {pids}")
                await run(f"fuser -k {expected_port}/tcp 2>/dev/null || kill -9 {' '.join(pids)} 2>/dev/null || true", timeout=10)
                log("  ✅ 已通过端口关闭服务")
                await asyncio.sleep(3)

            # 步骤3: 通过进程名查找并关闭（作为补充）
            project_type = plan.get('project_type', '')
            if project_type == 'python':
                proc_patterns = [
                    f"python.*{request.deploy_dir}",
                    f"python.*main.py",
                    f"uvicorn.*main:app"
                ]
            elif project_type == 'node':
                proc_patterns = [
                    f"node.*{request.deploy_dir}",
                    f"npm.*start"
                ]
            elif project_type == 'java':
                proc_patterns = [
                    f"java.*{request.deploy_dir}",
                    "java.*-jar"
                ]
            else:
                proc_patterns = []

            for pattern in proc_patterns:
                proc_check = await conn.run(f"pgrep -f '{pattern}'", check=False)
                if proc_check.stdout and proc_check.stdout.strip():
                    log(f"  ⚠️  发现匹配进程: {pattern}")
                    await run(f"pkill -f '{pattern}' 2>/dev/null || true", timeout=10)
                    log("  ✅ 已关闭相关进程")
                    await asyncio.sleep(2)

            # ── 5. 检查 deploy.sh ────────────────────────────────
            deploy_sh = await conn.run(
                f"test -f {request.deploy_dir}/deploy.sh && echo exists || echo no", check=False
            )
            if "exists" in (deploy_sh.stdout or ""):
                log("▶ 检测到 deploy.sh，直接执行...")
                r = await conn.run(
                    f"cd {request.deploy_dir} && chmod +x deploy.sh && bash deploy.sh 2>&1",
                    check=False
                )
                output = (r.stdout or r.stderr or "").strip()
                if output:
                    log(output[-2000:])

                # AI 分析 deploy.sh 执行结果，如果失败则提供修改建议
                if r.exit_status != 0 or ("error" in output.lower() or "failed" in output.lower() or "no such file" in output.lower()):
                    log("▶ deploy.sh 执行遇到问题，AI 分析中...")
                    analysis_prompt = f"""分析以下 deploy.sh 执行输出，判断是否需要修改脚本：

目标系统：{info.os_version}
deploy.sh 执行结果：
{output[-1500:]}

如果执行失败，请：
1. 指出具体错误原因（如路径错误、权限问题、依赖缺失等）
2. 提供具体的修复建议，包括需要修改的代码片段
3. 如果脚本中包含硬编码的路径（如 /etc/nginx/sites-available/），需要适配不同系统

如果执行成功，则回答："脚本执行正常"

回答格式：
问题：[具体问题]
原因：[原因分析]
修复建议：[具体修改建议]"""

                    try:
                        analysis = await LLM.chat([{"role": "user", "content": analysis_prompt}], max_tokens=500)
                        log(f"\n── deploy.sh 问题分析 ──\n{analysis}\n─────────────────")
                    except Exception as e:
                        log(f"⚠️  AI 分析失败: {e}")
            else:
                # ── 6. 按 AI 计划安装依赖 ────────────────────────
                install_steps = plan.get('install_steps') or []
                if request.install_cmd:
                    install_steps = [request.install_cmd]
                elif not install_steps:
                    if (await conn.run(f"test -f {request.deploy_dir}/requirements.txt", check=False)).exit_status == 0:
                        install_steps = ["pip3 install -r requirements.txt 2>/dev/null || python3 -m pip install -r requirements.txt --break-system-packages"]

                for step in install_steps:
                    log(f"▶ {step}")
                    await run(f"cd {request.deploy_dir} && {step}", timeout=300)

                # ── 7. 启动或重启 ─────────────────────────────────
                start_cmd = request.start_cmd or plan.get('start_cmd', '')
                if request.use_systemd and request.service_name and start_cmd:
                    if is_update:
                        log(f"▶ 更新：重启 systemd 服务 {request.service_name}")
                        await run(f"systemctl restart {request.service_name}")
                    else:
                        log(f"▶ 注册 systemd 服务: {request.service_name}")
                        svc = f"[Unit]\nDescription={request.service_name}\nAfter=network.target\n\n[Service]\nType=simple\nWorkingDirectory={request.deploy_dir}\nExecStart={start_cmd}\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n"
                        async with conn.start_sftp_client() as sftp:
                            async with sftp.open(f"/etc/systemd/system/{request.service_name}.service", 'w') as f:
                                await f.write(svc)
                        await run(f"systemctl daemon-reload && systemctl enable {request.service_name} && systemctl restart {request.service_name}")
                elif start_cmd:
                    if is_update:
                        # 更新时：杀掉旧进程，重新启动
                        old_proc = start_cmd.split()[0]
                        log(f"▶ 更新：重启应用进程")
                        await run(f"pkill -f '{old_proc}' 2>/dev/null; sleep 1; cd {request.deploy_dir} && nohup {start_cmd} > app.log 2>&1 &")
                    else:
                        log(f"▶ 后台启动: {start_cmd}")
                        await run(f"cd {request.deploy_dir} && nohup {start_cmd} > app.log 2>&1 &")

            # ── 8. 验证部署结果 ───────────────────────────────────
            log("\n▶ 验证部署结果...")
            await asyncio.sleep(4)

            health_cmd = plan.get('health_check') or \
                f"curl -s http://127.0.0.1:{plan.get('expected_port', 8000)}/health 2>/dev/null | grep -q 'ok' && echo 'OK' || " \
                f"(ss -tlnp | grep ':{plan.get('expected_port', 8000)}' 2>/dev/null || " \
                f"ps aux | grep -E 'python|node|java|gunicorn' | grep -v grep | grep -v agent.py | head -3)"

            health_r = await conn.run(health_cmd, check=False)
            health_out = (health_r.stdout or health_r.stderr or "").strip()

            # 检查 app.log 是否有错误
            app_log_r = await conn.run(
                f"tail -20 {request.deploy_dir}/app.log 2>/dev/null || "
                f"journalctl -u {request.service_name or 'app'} -n 10 --no-pager 2>/dev/null || echo ''",
                check=False
            )
            app_log_out = (app_log_r.stdout or "").strip()

            # AI 最终判断
            final_prompt = f"""判断以下应用部署是否成功，给出简洁结论：

健康检查结果：{health_out or '无输出'}
应用日志（最后20行）：{app_log_out[-500:] if app_log_out else '无'}

判断标准：
- 有进程在运行 → 成功
- 端口在监听 → 成功  
- 日志有 ModuleNotFoundError/ImportError/Error → 失败
- 日志有 started/running/listening → 成功

如果部署失败，请：
1. 明确指出失败原因
2. 如果是 deploy.sh 问题，提供具体的修改建议
3. 如果是依赖问题，提供安装命令
4. 如果是配置问题，提供修改方案

只回答：✅ 部署成功 或 ❌ 部署失败，然后说明原因，如果失败给出具体的修复建议（包括需要修改的文件路径和代码）。"""

            verdict = await LLM.chat([{"role": "user", "content": final_prompt}], max_tokens=400)
            log(f"\n── 部署验证结果 ──\n{verdict}\n─────────────────")

            # 如果验证失败且 deploy.sh 存在，提供详细修改指导
            if ("❌" in verdict or "失败" in verdict) and "exists" in (deploy_sh.stdout or ""):
                log("▶ 生成 deploy.sh 修改指导...")
                fix_prompt = f"""以下 deploy.sh 执行导致部署失败，请提供详细的修改指导：

目标系统：{info.os_version}
deploy.sh 错误输出：{output[-1000:] if 'output' in locals() else '无'}
应用日志错误：{app_log_out[-300:] if app_log_out else '无'}
验证结果：{verdict}

请提供：
1. deploy.sh 中需要修改的具体行数和代码
2. 修改后的完整代码片段
3. 需要额外执行的命令来修复问题
4. 如何验证修复是否成功

以以下格式回答：
━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 deploy.sh 修改指导
━━━━━━━━━━━━━━━━━━━━━━━━━━━

问题定位：
[具体问题描述]

需要修改的位置：
[文件名:行号] 原代码 → 修改后代码

修复代码：
```bash
# 完整的修复代码
```

执行命令：
```bash
# 需要执行的命令
```

验证方法：
[验证修复是否成功的方法]
━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

                try:
                    fix_guide = await LLM.chat([{"role": "user", "content": fix_prompt}], max_tokens=800)
                    log(f"\n{fix_guide}\n")
                except Exception as e:
                    log(f"⚠️  生成修改指导失败: {e}")

            if "❌" in verdict or "失败" in verdict:
                d.status = AppDeployStatus.FAILED
            else:
                d.status = AppDeployStatus.SUCCESS

        finally:
            conn.close()

    except Exception as e:
        log(f"❌ 部署失败: {e}")
        d.status = AppDeployStatus.FAILED
    finally:
        d.completed_at = datetime.now().isoformat()
        _save_app_deploys()  # 持久化部署结果
        _append_deploy_log(deploy_id, d.log)  # 追加日志文件


async def _run_task(task_id: str, request: TaskRequest):
    """后台执行：LLM 生成命令 → Agent 执行 → LLM 分析结果"""
    task = tasks[task_id]
    info = agents[request.agent_id]

    try:
        task.status = TaskStatus.RUNNING

        # 1. LLM 生成命令（os_hint 优先，否则用自动检测的 os_type）
        os_type = request.os_hint or info.os_type.value
        command = await LLM.generate_command(request.task, os_type)
        task.command = command

        if command.startswith("NEED_CLARIFICATION:"):
            task.status = TaskStatus.FAILED
            task.error = command
            task.completed_at = datetime.now().isoformat()
            return

        # 2. Agent 执行命令
        exec_result = await _agent_exec(info, command, request.timeout)
        success = exec_result.get("success", False)
        output = exec_result.get("output") or exec_result.get("error", "")
        task.output = output

        # 3. LLM 分析结果
        task.analysis = await LLM.analyze_result(
            request.task, command, output, success
        )

        task.status = TaskStatus.SUCCESS if success else TaskStatus.FAILED
        if not success:
            task.error = exec_result.get("error", "")

    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)
    finally:
        task.completed_at = datetime.now().isoformat()
        info.last_seen = datetime.now().isoformat()
        _save_tasks()  # 持久化任务状态


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
