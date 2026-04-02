"""AgentOps 控制端服务器"""
import uuid
import aiohttp
import os
import asyncio
import yaml
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 自动加载 .env
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()  # 强制覆盖

import asyncssh
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from models import (
    AgentInfo, AgentStatus, RemoteHost, TaskRequest, TaskResult, TaskStatus
)
from deployer import deploy, undeploy
import llm as LLM

HOSTS_FILE = Path(__file__).parent.parent / "hosts.yaml"
WEB_DIR = Path(__file__).parent.parent / "web"
AGENTS_FILE = Path(__file__).parent.parent / "agents.json"  # 持久化 Agent 列表

app = FastAPI(title="CyberAgentOps", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# 内存存储（可替换为数据库）
agents: Dict[str, AgentInfo] = {}
tasks: Dict[str, TaskResult] = {}

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

# token -> {username, role, perms}
_sessions: dict = {}

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

class GrantRequest(BaseModel):
    username: str
    perms: List[str]   # ["task", "host"]

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
    return {"ok": True, "message": "注册成功，等待管理员授权"}

@app.post("/auth/login")
async def login(req: LoginRequest):
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "admin123")
    # admin 登录
    if req.username == admin_user and req.password == admin_pass:
        token = secrets.token_hex(32)
        _sessions[token] = {"username": req.username, "role": "admin", "perms": ["task", "host"]}
        return {"token": token, "username": req.username, "role": "admin", "perms": ["task", "host"]}
    # 普通用户登录
    users = _load_users()
    u = users.get(req.username)
    if u and u["password"] == _hash_pw(req.password):
        token = secrets.token_hex(32)
        perms = u.get("perms", [])
        _sessions[token] = {"username": req.username, "role": "user", "perms": perms}
        return {"token": token, "username": req.username, "role": "user", "perms": perms}
    raise HTTPException(status_code=401, detail="用户名或密码错误")

@app.post("/auth/logout")
async def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "")
    _sessions.pop(token, None)
    return {"ok": True}

@app.get("/auth/users")
async def list_users(authorization: str = Header(default="")):
    """admin 查看所有用户"""
    _check_perm(authorization, "admin")
    users = _load_users()
    return [{"username": k, "perms": v.get("perms", [])} for k, v in users.items()]

@app.post("/auth/grant")
async def grant_perm(req: GrantRequest, authorization: str = Header(default="")):
    """admin 授权"""
    _check_perm(authorization, "admin")
    users = _load_users()
    if req.username not in users:
        raise HTTPException(status_code=404, detail="用户不存在")
    users[req.username]["perms"] = req.perms
    _save_users(users)
    # 更新已登录的 session
    for s in _sessions.values():
        if s["username"] == req.username:
            s["perms"] = req.perms
    return {"ok": True}

def _get_session(authorization: str) -> Optional[dict]:
    token = authorization.replace("Bearer ", "")
    return _sessions.get(token)

def _check_perm(authorization: str, perm: str):
    s = _get_session(authorization)
    if not s:
        raise HTTPException(status_code=401, detail="请先登录")
    if perm == "admin" and s["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if perm not in ("admin",) and perm not in s.get("perms", []) and s["role"] != "admin":
        raise HTTPException(status_code=403, detail=f"无权限: {perm}")


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
async def list_hosts():
    """获取所有配置的目标机器"""
    hosts = _read_hosts()
    return [{"name": k, **v} for k, v in hosts.items()]


@app.post("/hosts")
async def add_host(entry: HostEntry, authorization: str = Header(default="")):
    """新增目标机器"""
    _check_perm(authorization, "host")
    hosts = _read_hosts()
    if entry.name in hosts:
        raise HTTPException(status_code=400, detail=f"主机 '{entry.name}' 已存在")
    hosts[entry.name] = entry.model_dump(exclude={"name"}, exclude_none=True)
    _write_hosts(hosts)
    return {"message": "添加成功", "name": entry.name}


@app.put("/hosts/{name}")
async def update_host(name: str, entry: HostEntry, authorization: str = Header(default="")):
    """更新目标机器配置"""
    _check_perm(authorization, "host")
    hosts = _read_hosts()
    if name not in hosts:
        raise HTTPException(status_code=404, detail="主机不存在")
    hosts[name] = entry.model_dump(exclude={"name"}, exclude_none=True)
    _write_hosts(hosts)
    return {"message": "更新成功"}


@app.delete("/hosts/{name}")
async def delete_host(name: str, authorization: str = Header(default="")):
    """删除目标机器"""
    _check_perm(authorization, "host")
    hosts = _read_hosts()
    if name not in hosts:
        raise HTTPException(status_code=404, detail="主机不存在")
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
    """SSH 登录目标机器，自动检测 OS，上传并启动 Agent（需要 host 权限）"""
    _check_perm(authorization, "host")
    try:
        # 同名 Agent 已存在则先移除
        if host.name:
            existing = next((a for a in agents.values() if a.name == host.name), None)
            if existing:
                del agents[existing.agent_id]

        info = await deploy(host)
        info.password = host.password
        info.ssh_key = host.ssh_key
        agents[info.agent_id] = info

        # 部署完立即采集一次指标（后台，不阻塞响应）
        asyncio.create_task(_collect_metrics_now(info.agent_id))
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/agents/{agent_id}")
async def remove_agent(agent_id: str):
    """停止并清理目标机器上的 Agent"""
    info = _get_agent(agent_id)
    host = RemoteHost(
        host=info.host, port=info.port,
        username=info.username, deploy_dir=info.deploy_dir
    )
    await undeploy(host)
    del agents[agent_id]
    return {"message": f"Agent {agent_id} 已移除"}


@app.get("/agents", response_model=List[AgentInfo])
async def list_agents():
    return list(agents.values())


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
    return {"online": bool(result), "info": result}


# ── 任务下发 ─────────────────────────────────────────────────

@app.post("/tasks", response_model=TaskResult)
async def submit_task(request: TaskRequest, background_tasks: BackgroundTasks,
                      authorization: str = Header(default="")):
    """下发自然语言任务到指定 Agent（需要 task 权限）"""
    _check_perm(authorization, "task")
    info = _get_agent(request.agent_id)
    if info.status == AgentStatus.OFFLINE:
        raise HTTPException(status_code=503, detail="Agent 离线")

    task_id = uuid.uuid4().hex[:12]
    task = TaskResult(
        task_id=task_id,
        agent_id=request.agent_id,
        status=TaskStatus.PENDING,
        task=request.task,
        created_at=datetime.now().isoformat(),
    )
    tasks[task_id] = task
    background_tasks.add_task(_run_task, task_id, request)
    return task


@app.get("/tasks", response_model=List[TaskResult])
async def list_tasks(agent_id: Optional[str] = None):
    result = list(tasks.values())
    if agent_id:
        result = [t for t in result if t.agent_id == agent_id]
    return sorted(result, key=lambda t: t.created_at, reverse=True)


@app.get("/tasks/{task_id}", response_model=TaskResult)
async def get_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tasks[task_id]


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


if __name__ == "__main__":
    import uvicorn
    # 挂载 Web 静态文件
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
