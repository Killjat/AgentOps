"""AgentOps 控制端服务器"""
import uuid
import aiohttp
import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

import asyncssh
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from models import (
    AgentInfo, AgentStatus, RemoteHost, TaskRequest, TaskResult, TaskStatus
)
from deployer import deploy, undeploy
import llm as LLM

app = FastAPI(title="AgentOps", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# 内存存储（可替换为数据库）
agents: Dict[str, AgentInfo] = {}
tasks: Dict[str, TaskResult] = {}


# ── 部署管理 ─────────────────────────────────────────────────

@app.post("/agents/deploy", response_model=AgentInfo)
async def deploy_agent(host: RemoteHost):
    """SSH 登录目标机器，自动检测 OS，上传并启动 Agent"""
    try:
        info = await deploy(host)
        # 保存 SSH 凭据，供后续建隧道使用
        info.password = host.password
        info.ssh_key = host.ssh_key
        agents[info.agent_id] = info
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
async def submit_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """下发自然语言任务到指定 Agent"""
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
                  keepalive_interval=15,   # 每 15 秒发一次 keepalive
                  keepalive_count_max=6)   # 最多允许 6 次无响应
    if info.password:
        kwargs["password"] = info.password
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


async def _run_task(task_id: str, request: TaskRequest):
    """后台执行：LLM 生成命令 → Agent 执行 → LLM 分析结果"""
    task = tasks[task_id]
    info = agents[request.agent_id]

    try:
        task.status = TaskStatus.RUNNING

        # 1. LLM 生成命令
        command = await LLM.generate_command(request.task, info.os_type.value)
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
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
