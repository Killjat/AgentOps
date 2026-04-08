"""Agent 管理路由 + WebSocket + SSH 工具函数"""
import asyncio
import json
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

import asyncssh
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from models import (
    AgentInfo, AgentStatus, ConnectionType, DeviceType, OSType, RemoteHost
)
from core.state import agents, servers, _ws_connections, _ws_pending
from core.storage import _save_agents
from deployer import deploy, undeploy, update
from routers.auth import _check_owner, _check_perm, _get_caller, _is_admin

router = APIRouter(prefix="/agents", tags=["agents"])

# 存储 agent 部署任务日志 {deploy_id: {"log": str, "status": str, "agent_id": str}}
_agent_deploy_tasks: dict = {}


# ── 工具函数 ─────────────────────────────────────────────────────

def _get_agent(agent_id: str) -> AgentInfo:
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agents[agent_id]


def _ssh_kwargs(info: AgentInfo) -> dict:
    """构建 asyncssh 连接参数"""
    server = servers.get(info.server_id)
    if not server:
        raise ValueError(f"服务器 {info.server_id} 不存在")
    kwargs = dict(
        host=server.host, port=server.port,
        username=server.username, known_hosts=None,
        keepalive_interval=15, keepalive_count_max=6
    )
    if server.password:
        kwargs["password"] = server.password
        kwargs["preferred_auth"] = "password,keyboard-interactive"
    if server.ssh_key:
        kwargs["client_keys"] = [server.ssh_key]
    return kwargs


@asynccontextmanager
async def _ssh_tunnel(info: AgentInfo):
    """建立 SSH 本地端口转发隧道"""
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
            if info.os_type == OSType.WINDOWS:
                r = await conn.run(
                    'tasklist /FI "IMAGENAME eq pythonw.exe" /FI "STATUS eq running"',
                    check=False, encoding="latin-1", errors="replace"
                )
                running = "pythonw.exe" in (r.stdout or "")
                info_r = await conn.run("hostname", check=False, encoding="latin-1", errors="replace")
                hostname = (info_r.stdout or "").strip()
                os_str = "Windows"
            else:
                r = await conn.run("pgrep -f 'agent.py' && echo running || echo stopped", check=False)
                running = "running" in (r.stdout or "")
                info_r = await conn.run("uname -n && uname -sr", check=False)
                lines = (info_r.stdout or "").strip().splitlines()
                hostname = lines[0] if lines else ""
                os_str = lines[1] if len(lines) > 1 else ""
            return {
                "pong": running,
                "info": {
                    "hostname": hostname,
                    "os": os_str,
                    "os_version": info.os_version,
                }
            }
        finally:
            conn.close()
    except Exception:
        return None


async def _agent_exec(info: AgentInfo, command: str, timeout: int) -> dict:
    """执行命令：优先走 WebSocket，降级走 SSH"""
    if info.agent_id in _ws_connections:
        try:
            resp = await _ws_call(info.agent_id, {"type": "exec", "command": command, "timeout": timeout}, timeout=timeout + 5)
            return {"success": resp.get("success", False), "output": resp.get("output", ""), "error": resp.get("error", "")}
        except HTTPException:
            pass  # 降级到 SSH

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


async def _ws_call(agent_id: str, msg: dict, timeout: int = 60) -> dict:
    """通过 WebSocket 向 agent 发送消息并等待响应，agent 不在本地时转发给对端"""
    ws = _ws_connections.get(agent_id)
    if not ws:
        # 尝试通过对端代理执行
        import os, aiohttp, ssl
        peer_url = os.getenv("PEER_URL", "").strip()
        if peer_url:
            try:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        f"{peer_url}/sync/proxy",
                        json={"agent_id": agent_id, "msg": msg, "timeout": timeout},
                        timeout=aiohttp.ClientTimeout(total=timeout + 10)
                    ) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        text = await resp.text()
                        raise HTTPException(status_code=resp.status, detail=f"对端代理失败: {text[:100]}")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"对端代理异常: {e}")
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


# ── WebSocket 端点 ────────────────────────────────────────────────

async def ws_agent_endpoint(websocket: WebSocket, agent_id: str):
    """WebSocket 连接处理（注册在 main.py）"""
    await websocket.accept()
    _ws_connections[agent_id] = websocket
    _ws_pending.setdefault(agent_id, {})
    print(f"[WS] Agent {agent_id} 已连接")

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
                        agents[agent_id].status = AgentStatus.ONLINE
                        agents[agent_id].last_seen = datetime.now().isoformat()
                    else:
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
                    asyncio.create_task(_collect_metrics_now(agent_id))

                elif msg_type == "pong":
                    if agent_id in agents:
                        agents[agent_id].last_seen = datetime.now().isoformat()

                elif msg_type == "metrics_push":
                    metrics = msg.get("metrics", {})
                    if metrics and agent_id in agents:
                        agents[agent_id].metrics = metrics
                        agents[agent_id].last_seen = datetime.now().isoformat()
                        agents[agent_id].status = AgentStatus.ONLINE
                        _save_agents()

                elif task_id and task_id in _ws_pending.get(agent_id, {}):
                    fut = _ws_pending[agent_id].pop(task_id)
                    if not fut.done():
                        fut.set_result(msg)

            except Exception as e:
                print(f"[WS] 消息处理错误: {e}")

    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.pop(agent_id, None)
        pending = _ws_pending.pop(agent_id, {})
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(f"Agent {agent_id} 断线"))
        if agent_id in agents:
            agents[agent_id].status = AgentStatus.OFFLINE
        print(f"[WS] Agent {agent_id} 断开连接，取消 {len(pending)} 个待处理任务")


# ── 部署相关 ─────────────────────────────────────────────────────

class AgentDeployRequest(BaseModel):
    server_id: str
    name: str = ""


@router.post("/deploy")
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


async def _run_agent_deploy(deploy_id: str, req: AgentDeployRequest, server, caller: str):
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


@router.get("/deploy/{deploy_id}/stream")
async def stream_agent_deploy_log(deploy_id: str):
    """SSE 实时推送 Agent 部署日志"""
    if deploy_id not in _agent_deploy_tasks:
        raise HTTPException(status_code=404, detail="部署任务不存在")

    async def event_generator():
        last_len = 0
        for _ in range(300):
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


# ── Agent CRUD ────────────────────────────────────────────────────

@router.delete("/{agent_id}")
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


@router.post("/{agent_id}/update")
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


@router.get("", response_model=List[AgentInfo])
async def list_agents(authorization: str = Header(default="")):
    """admin 看全部，其他用户只看自己的"""
    all_agents = list(agents.values())
    if _is_admin(authorization):
        return all_agents
    caller = _get_caller(authorization)
    return [a for a in all_agents if a.owner == caller]


@router.get("/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: str):
    return _get_agent(agent_id)


@router.get("/{agent_id}/ports")
async def get_agent_ports(agent_id: str, authorization: str = Header(default="")):
    """获取 agent 机器上已占用的端口"""
    _check_perm(authorization, "login")
    _get_agent(agent_id)
    try:
        result = await _ws_call(agent_id, {
            "type": "exec",
            "command": (
                "ss -tlnp 2>/dev/null | awk 'NR>1{print $4}' | grep -oP ':\\K\\d+' | sort -n | uniq || "
                "netstat -tlnp 2>/dev/null | awk 'NR>2{print $4}' | grep -oP ':\\K\\d+' | sort -n | uniq || "
                "lsof -i -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | grep -oP ':\\K\\d+' | sort -n | uniq"
            )
        }, timeout=10)
        ports = [int(p) for p in (result.get("output") or "").splitlines() if p.strip().isdigit()]
        return {"ports": sorted(set(ports))}
    except Exception:
        return {"ports": []}


@router.post("/{agent_id}/metrics")
async def receive_metrics(agent_id: str, payload: dict):
    """接收 Agent 上报的系统指标"""
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    info = agents[agent_id]
    info.metrics = payload.get("metrics", payload)
    info.last_seen = datetime.now().isoformat()
    info.status = AgentStatus.ONLINE
    _save_agents()
    return {"ok": True}


@router.get("/{agent_id}/metrics")
async def get_metrics(agent_id: str):
    """获取 Agent 最新上报的指标"""
    info = _get_agent(agent_id)
    if not info.metrics:
        raise HTTPException(status_code=404, detail="暂无上报数据")
    return info.metrics


@router.post("/{agent_id}/ping")
async def ping_agent(agent_id: str):
    """检查 Agent 是否在线"""
    info = _get_agent(agent_id)
    if info.connection_type == ConnectionType.AGENT_PUSH:
        online = agent_id in _ws_connections
        info.status = AgentStatus.ONLINE if online else AgentStatus.OFFLINE
        _save_agents()
        return {"online": online, "info": None}
    result = await _agent_get(info, "/ping")
    info.status = AgentStatus.ONLINE if result else AgentStatus.OFFLINE
    info.last_seen = datetime.now().isoformat()
    _save_agents()
    return {"online": bool(result), "info": result}
