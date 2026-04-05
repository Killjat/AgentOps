"""任务管理路由"""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from models import TaskRequest, TaskResult, TaskStatus
from core.state import agents, tasks
from core.storage import _save_tasks
from routers.auth import _check_owner, _check_perm, _get_caller, _is_admin
from routers.agents import _agent_exec, _get_agent
import llm as LLM

router = APIRouter(prefix="/tasks", tags=["tasks"])


class ChatRequest(BaseModel):
    task_id: str
    message: str
    execute: bool = False


@router.post("", response_model=TaskResult)
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
    _save_tasks()
    background_tasks.add_task(_run_task, task_id, request)
    return task


@router.get("", response_model=List[TaskResult])
async def list_tasks(agent_id: Optional[str] = None,
                     authorization: str = Header(default="")):
    result = list(tasks.values())
    if not _is_admin(authorization):
        caller = _get_caller(authorization)
        result = [t for t in result if t.owner == caller]
    if agent_id:
        result = [t for t in result if t.agent_id == agent_id]
    return sorted(result, key=lambda t: t.created_at, reverse=True)


@router.get("/{task_id}", response_model=TaskResult)
async def get_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tasks[task_id]


@router.post("/{task_id}/chat")
async def chat_with_task(task_id: str, req: ChatRequest,
                         authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = tasks[task_id]
    _check_owner(authorization, task.owner, "任务")

    info = agents.get(task.agent_id)
    if not info:
        raise HTTPException(status_code=404, detail="Agent 不存在")

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
        exec_system = context + "\n用户想执行操作，请只返回一条可直接执行的 shell 命令，不要任何解释，不要 markdown 格式。"
        cmd_messages = [{"role": "system", "content": exec_system}]
        for msg in task.conversation:
            if msg["role"] != "system":
                cmd_messages.append({"role": msg["role"], "content": msg["content"]})
        cmd_messages.append({"role": "user", "content": req.message})

        command = await LLM.chat(cmd_messages, max_tokens=200)
        command = command.strip().strip('`').strip()
        if command.startswith("bash\n") or command.startswith("sh\n"):
            command = command.split("\n", 1)[1].strip()

        exec_result = await _agent_exec(info, command, 60)
        success = exec_result.get("success", False)
        output = exec_result.get("output") or exec_result.get("error", "")

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


async def _run_task(task_id: str, request: TaskRequest):
    """后台执行：LLM 生成命令 → Agent 执行 → LLM 分析结果"""
    task = tasks[task_id]
    info = agents[request.agent_id]

    try:
        task.status = TaskStatus.RUNNING

        os_type = request.os_hint or info.os_type.value
        command = await LLM.generate_command(request.task, os_type)
        task.command = command

        if command.startswith("NEED_CLARIFICATION:"):
            task.status = TaskStatus.FAILED
            task.error = command
            task.completed_at = datetime.now().isoformat()
            return

        exec_result = await _agent_exec(info, command, request.timeout)
        success = exec_result.get("success", False)
        output = exec_result.get("output") or exec_result.get("error", "")
        task.output = output

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
        _save_tasks()
