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
from fastapi.responses import FileResponse
from pydantic import BaseModel
import sys
sys.path.insert(0, str(Path(__file__).parent))

from models import (
    AgentInfo, AgentStatus, RemoteHost, TaskRequest, TaskResult, TaskStatus,
    ChatRequest, AppDeployRequest, AppDeployResult, AppDeployStatus
)
from deployer import deploy, undeploy
import llm as LLM

HOSTS_FILE = Path(__file__).parent.parent / "hosts.yaml"
WEB_DIR = Path(__file__).parent.parent / "web"
AGENTS_FILE = Path(__file__).parent.parent / "agents.json"
TASKS_FILE = Path(__file__).parent.parent / "tasks.json"
APP_DEPLOYS_FILE = Path(__file__).parent.parent / "app_deploys.json"
LOGS_DIR = Path(__file__).parent.parent / "logs"

# 内存存储（可替换为数据库）
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
    global agents, tasks, app_deploys

    # 加载 agents
    logger.info(f"[加载] 从 {AGENTS_FILE} 加载 agents...")
    agents_data = _load_json(AGENTS_FILE, {})
    logger.info(f"[加载] agents 文件中有 {len(agents_data)} 条记录")
    for agent_id, data in agents_data.items():
        try:
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
            app_deploys[deploy_id] = AppDeployResult(**data)
            logger.info(f"[加载] 成功加载 deploy: {deploy_id}")
        except Exception as e:
            logger.error(f"[加载] 加载 deploy {deploy_id} 失败: {e}")
    logger.info(f"[加载] app_deploys 加载完成，共 {len(app_deploys)} 个")

    print(f"[持久化] 已加载: {len(agents)} 个 Agent, {len(tasks)} 个任务, {len(app_deploys)} 个应用部署")

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


# ── Hosts 配置管理 ────────────────────────────────────────────

class HostEntry(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    deploy_dir: str = "/opt/agentops"


def _read_hosts() -> dict:
    if not HOSTS_FILE.exists():
        return {}
    with open(HOSTS_FILE) as f:
        return (yaml.safe_load(f) or {}).get("hosts", {})


def _write_hosts(hosts: dict):
    HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HOSTS_FILE, "w") as f:
        yaml.dump({"hosts": hosts}, f, allow_unicode=True, default_flow_style=False)


@app.get("/hosts")
async def list_hosts(authorization: str = Header(default="")):
    """获取目标机器列表：admin 看全部，其他用户只看自己的"""
    hosts = _read_hosts()
    caller = _get_caller(authorization)
    if _is_admin(authorization):
        return [{"name": k, **v} for k, v in hosts.items()]
    # 非 admin 只返回自己添加的（owner 字段匹配）
    return [{"name": k, **v} for k, v in hosts.items()
            if v.get("owner") == caller]


@app.post("/hosts")
async def add_host(entry: HostEntry, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)
    hosts = _read_hosts()
    if entry.name in hosts:
        raise HTTPException(status_code=400, detail=f"主机 '{entry.name}' 已存在")
    data = entry.model_dump(exclude={"name"}, exclude_none=True)
    data["owner"] = caller          # 记录 owner
    hosts[entry.name] = data
    _write_hosts(hosts)
    return {"message": "添加成功", "name": entry.name}


@app.put("/hosts/{name}")
async def update_host(name: str, entry: HostEntry, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    hosts = _read_hosts()
    if name not in hosts:
        raise HTTPException(status_code=404, detail="主机不存在")
    # 只有 owner 或 admin 可以修改
    if not _is_admin(authorization) and hosts[name].get("owner") != _get_caller(authorization):
        raise HTTPException(status_code=403, detail="无权修改他人的机器")
    data = entry.model_dump(exclude={"name"}, exclude_none=True)
    data["owner"] = hosts[name].get("owner", _get_caller(authorization))
    hosts[name] = data
    _write_hosts(hosts)
    return {"message": "更新成功"}


@app.delete("/hosts/{name}")
async def delete_host(name: str, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    hosts = _read_hosts()
    if name not in hosts:
        raise HTTPException(status_code=404, detail="主机不存在")
    if not _is_admin(authorization) and hosts[name].get("owner") != _get_caller(authorization):
        raise HTTPException(status_code=403, detail="无权删除他人的机器")
    del hosts[name]
    _write_hosts(hosts)
    return {"message": "删除成功"}


@app.post("/hosts/test")
async def test_host(entry: HostEntry):
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


# ── 部署管理 ─────────────────────────────────────────────────

@app.post("/agents/deploy", response_model=AgentInfo)
async def deploy_agent(host: RemoteHost, background_tasks: BackgroundTasks,
                       authorization: str = Header(default="")):
    """部署 Agent，需要登录（任何登录用户都可以）"""
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)
    try:
        if host.name:
            existing = next((a for a in agents.values() if a.name == host.name and a.owner == caller), None)
            if existing:
                del agents[existing.agent_id]

        info = await deploy(host)
        info.password = host.password
        info.ssh_key = host.ssh_key
        info.owner = caller          # 绑定 owner
        agents[info.agent_id] = info
        _save_agents()  # 持久化
        asyncio.create_task(_collect_metrics_now(info.agent_id))
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/agents/{agent_id}")
async def remove_agent(agent_id: str, authorization: str = Header(default="")):
    info = _get_agent(agent_id)
    _check_owner(authorization, info.owner, "Agent")
    info = _get_agent(agent_id)
    host = RemoteHost(
        host=info.host, port=info.port,
        username=info.username, deploy_dir=info.deploy_dir
    )
    await undeploy(host)
    del agents[agent_id]
    _save_agents()  # 持久化
    return {"message": f"Agent {agent_id} 已移除"}


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
    kwargs = dict(host=info.host, port=info.port,
                  username=info.username, known_hosts=None,
                  keepalive_interval=15,
                  keepalive_count_max=6)
    if info.password:
        kwargs["password"] = info.password
        kwargs["preferred_auth"] = "password,keyboard-interactive"
    if info.ssh_key:
        kwargs["client_keys"] = [info.ssh_key]
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
            r = await conn.run("pgrep -f 'agent.py' && echo running || echo stopped", check=False)
            running = "running" in (r.stdout or "")
            # 顺便获取系统信息
            info_r = await conn.run("uname -n && uname -sr", check=False)
            lines = (info_r.stdout or "").strip().splitlines()
            return {
                "pong": running,
                "info": {
                    "hostname": lines[0] if lines else "",
                    "os": lines[1] if len(lines) > 1 else "",
                    "os_version": info.os_version,
                }
            }
        finally:
            conn.close()
    except Exception:
        return None


async def _agent_exec(info: AgentInfo, command: str, timeout: int) -> dict:
    """直接通过 SSH 在目标机器上执行命令（无需 HTTP 隧道）"""
    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=timeout + 10
            )
            output = result.stdout or result.stderr or ""
            return {"success": result.exit_status == 0, "output": output, "error": ""}
        finally:
            conn.close()
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"命令执行超时（{timeout}s）"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


async def _collect_metrics_now(agent_id: str):
    """部署完成后立即通过 SSH 采集一次系统指标"""
    await asyncio.sleep(3)
    if agent_id not in agents:
        return
    info = agents[agent_id]
    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            metrics = {}

            async def run(cmd):
                r = await conn.run(cmd, check=False)
                return (r.stdout or "").strip()

            # 基础信息
            metrics["timestamp"] = datetime.now().isoformat()
            metrics["hostname"] = await run("hostname")
            metrics["os"] = await run("uname -s")
            metrics["os_version"] = await run(
                "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"' "
                "|| uname -r"
            )

            # CPU 使用率（两次采样）
            try:
                v1 = list(map(int, (await run("cat /proc/stat | head -1")).split()[1:]))
                await asyncio.sleep(1)
                v2 = list(map(int, (await run("cat /proc/stat | head -1")).split()[1:]))
                metrics["cpu_usage"] = round(100 * (1 - (v2[3]-v1[3]) / (sum(v2)-sum(v1))), 1)
            except Exception:
                metrics["cpu_usage"] = -1

            # 硬件信息
            hw = {}
            hw["cpu_model"] = await run(
                "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2 | xargs"
            )
            hw["cpu_cores"] = await run("nproc")
            mem_kb = await run("grep MemTotal /proc/meminfo | awk '{print $2}'")
            hw["memory_mb"] = round(int(mem_kb) / 1024) if mem_kb.isdigit() else 0
            hw["board_name"] = await run("cat /sys/class/dmi/id/board_name 2>/dev/null || echo N/A")
            hw["board_serial"] = await run("cat /sys/class/dmi/id/board_serial 2>/dev/null || echo N/A")

            # 磁盘 ID
            disk_ids_raw = await run("ls /dev/disk/by-id/ 2>/dev/null")
            hw["disk_ids"] = [d for d in disk_ids_raw.splitlines() if d and "part" not in d][:6]
            if not hw["disk_ids"]:
                lsblk = await run("lsblk -d -o NAME,MODEL,SERIAL 2>/dev/null | tail -n +2")
                hw["disk_ids"] = lsblk.splitlines()[:4]

            # MAC 地址
            macs = {}
            ip_out = await run("ip link show 2>/dev/null")
            iface = None
            import re as _re
            for line in ip_out.splitlines():
                m = _re.match(r'\d+:\s+(\S+):', line)
                if m:
                    iface = m.group(1).rstrip(":")
                m2 = _re.search(r'link/ether\s+([0-9a-f:]{17})', line, _re.I)
                if m2 and iface and iface != "lo":
                    macs[iface] = m2.group(1)
            hw["mac_addresses"] = macs

            # 硬件指纹
            import hashlib as _hl
            raw = "|".join([hw.get("cpu_model",""), hw.get("board_serial",""),
                            str(hw.get("disk_ids","")), str(macs), metrics.get("hostname","")])
            hw["hw_fingerprint"] = _hl.sha256(raw.encode()).hexdigest()[:16]
            metrics["hardware"] = hw

            # 磁盘使用
            disk = []
            df_out = await run("df -h")
            for line in df_out.splitlines()[1:]:
                p = line.split()
                if len(p) >= 6:
                    disk.append({"mount": p[5], "size": p[1], "used": p[2],
                                 "avail": p[3], "use_pct": p[4]})
            metrics["disk"] = disk[:5]

            # 网络 IP
            ips = {"hostname": metrics["hostname"]}
            ip4_out = await run("ip -4 addr show")
            for line in ip4_out.splitlines():
                m = _re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
                if m and not m.group(1).startswith("127."):
                    ips["eth"] = m.group(1)
                    break
            pub = await run("curl -s --max-time 3 https://api.ipify.org 2>/dev/null || wget -qO- --timeout=3 https://api.ipify.org 2>/dev/null")
            if pub:
                ips["public"] = pub
            metrics["network"] = ips

            # 网络 IO
            net_io = {}
            def parse_net(raw):
                s = {}
                for line in raw.splitlines()[2:]:
                    p = line.split()
                    if len(p) >= 10:
                        ifc = p[0].rstrip(":")
                        if ifc != "lo":
                            s[ifc] = {"rx": int(p[1]), "tx": int(p[9])}
                return s
            s1 = parse_net(await run("cat /proc/net/dev"))
            await asyncio.sleep(1)
            s2 = parse_net(await run("cat /proc/net/dev"))
            for ifc in s1:
                if ifc in s2:
                    net_io[ifc] = {
                        "rx_kbps": round((s2[ifc]["rx"] - s1[ifc]["rx"]) / 1024, 1),
                        "tx_kbps": round((s2[ifc]["tx"] - s1[ifc]["tx"]) / 1024, 1),
                        "rx_bytes_total": s2[ifc]["rx"],
                        "tx_bytes_total": s2[ifc]["tx"],
                    }
            metrics["network_io"] = net_io

            info.metrics = metrics
            info.last_seen = datetime.now().isoformat()
            print(f"[metrics] {info.name or agent_id} 初始采集完成")

        finally:
            conn.close()
    except Exception as e:
        print(f"[metrics] {info.name or agent_id} 采集失败: {e}")


# ── 应用部署 ─────────────────────────────────────────────────

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
目标服务器：{info.host} ({info.os_version})
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
            check_dir = await conn.run(f"test -d {request.deploy_dir}/.git && echo exists", check=False)
            is_update = "exists" in (check_dir.stdout or "")
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

            # ── 4. 检查 deploy.sh ────────────────────────────────
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
                # ── 5. 按 AI 计划安装依赖 ────────────────────────
                install_steps = plan.get('install_steps') or []
                if request.install_cmd:
                    install_steps = [request.install_cmd]
                elif not install_steps:
                    if (await conn.run(f"test -f {request.deploy_dir}/requirements.txt", check=False)).exit_status == 0:
                        install_steps = ["pip3 install -r requirements.txt 2>/dev/null || python3 -m pip install -r requirements.txt --break-system-packages"]

                for step in install_steps:
                    log(f"▶ {step}")
                    await run(f"cd {request.deploy_dir} && {step}", timeout=300)

                # ── 6. 启动或重启 ─────────────────────────────────
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

            # ── 7. 验证部署结果 ───────────────────────────────────
            log("\n▶ 验证部署结果...")
            await asyncio.sleep(4)

            health_cmd = plan.get('health_check') or \
                f"ss -tlnp | grep ':{plan.get('expected_port', 8000)}' 2>/dev/null || " \
                f"ps aux | grep -E 'python|node|java|gunicorn' | grep -v grep | grep -v agent.py | head -3"

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
    port = int(os.getenv("SERVER_PORT", "8443"))
    ssl_keyfile = os.getenv("SSL_KEYFILE", "/etc/ssl/private/server.key")
    ssl_certfile = os.getenv("SSL_CERTFILE", "/etc/ssl/certs/server.crt")

    # 检查 SSL 证书是否存在
    if os.path.exists(ssl_keyfile) and os.path.exists(ssl_certfile):
        logger.info(f"Starting HTTPS server on {host}:{port}")
        uvicorn.run(app, host=host, port=port,
                    ssl_keyfile=ssl_keyfile, ssl_certfile=ssl_certfile)
    else:
        logger.warning(f"SSL certificates not found at {ssl_keyfile} and {ssl_certfile}")
        logger.warning("Falling back to HTTP mode (for development only)")
        uvicorn.run(app, host=host, port=port)
