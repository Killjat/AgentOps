"""Swarm Coordinator - 对外暴露的主入口，串联 planner + executor"""
import sys
import os
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from swarm_models import SwarmTask, SwarmTaskRequest, SwarmTaskStatus, SubTask
from planner import plan_swarm_task
from executor import SwarmExecutor

logger = logging.getLogger(__name__)

# 内存存储（与 server/core/state.py 同进程，直接 import）
_swarm_tasks: Dict[str, SwarmTask] = {}


async def run_swarm(req: SwarmTaskRequest, owner: str = "") -> SwarmTask:
    """创建并执行一个 swarm 任务"""
    # 延迟 import，避免循环依赖
    from core import state
    from routers.agents import _ws_call

    swarm_task_id = f"swarm-{uuid.uuid4().hex[:8]}"

    task = SwarmTask(
        swarm_task_id=swarm_task_id,
        owner=owner,
        goal=req.goal,
        agent_ids=req.agent_ids,
        status=SwarmTaskStatus.PLANNING,
        created_at=datetime.now().isoformat(),
    )
    _swarm_tasks[swarm_task_id] = task

    # 1. 获取 Agent 信息
    agents_info = _get_agents_info(req.agent_ids, state)

    # 2. LLM 规划
    logger.info(f"[swarm:{swarm_task_id}] 开始规划，目标: {req.goal}")
    task = await plan_swarm_task(task, agents_info)

    if not task.subtasks:
        task.status = SwarmTaskStatus.FAILED
        task.summary = "规划失败：未生成任何子任务"
        return task

    logger.info(f"[swarm:{swarm_task_id}] 规划完成，共 {len(task.subtasks)} 个子任务")

    # 3. 执行
    async def dispatch(agent_id: str, subtask: SubTask) -> tuple:
        try:
            # 如果没有预生成的命令，用 LLM 把自然语言指令转成 shell 命令
            cmd = subtask.command
            if not cmd:
                from llm import generate_command
                agent = state.agents.get(agent_id)
                os_type = str(agent.os_type) if agent else "Linux"
                cmd = await generate_command(subtask.instruction, os_type=os_type)
                subtask.command = cmd

            result = await _ws_call(agent_id, {
                "type": "exec",
                "task_id": subtask.subtask_id,
                "command": cmd,
                "timeout": 120,
            }, timeout=180)
            return result.get("success", False), result.get("output", ""), result.get("error", "")
        except Exception as e:
            return False, "", str(e)

    executor = SwarmExecutor(dispatch_fn=dispatch)
    task = await executor.run(task)

    # 4. 生成 AI 汇报
    task.summary = await _ai_report(task)
    task.completed_at = datetime.now().isoformat()
    logger.info(f"[swarm:{swarm_task_id}] 完成，状态: {task.status}")
    return task


def get_task(swarm_task_id: str) -> Optional[SwarmTask]:
    return _swarm_tasks.get(swarm_task_id)


def list_tasks() -> List[SwarmTask]:
    return list(_swarm_tasks.values())


def _get_agents_info(agent_ids: List[str], state) -> List[dict]:
    result = []
    for aid in agent_ids:
        agent = state.agents.get(aid)
        if agent:
            result.append({
                "agent_id": aid,
                "os_type": agent.os_type,
                "hostname": (agent.metrics or {}).get("os_info", {}).get("hostname", ""),
                "status": agent.status,
            })
        else:
            result.append({"agent_id": aid, "os_type": "unknown", "status": "unknown"})
    return result


def _summarize(task: SwarmTask) -> str:
    lines = [f"目标: {task.goal}", f"状态: {task.status}", ""]
    for st in task.subtasks:
        icon = {"success": "✅", "failed": "❌", "skipped": "⏭️", "pending": "⏳"}.get(st.status, "•")
        lines.append(f"{icon} [{st.agent_id}] {st.instruction}")
        if st.error:
            lines.append(f"   错误: {st.error}")
        elif st.output:
            preview = (st.output or "")[:100]
            lines.append(f"   输出: {preview}{'...' if len(st.output or '') > 100 else ''}")
    return "\n".join(lines)


async def _ai_report(task: SwarmTask) -> str:
    """用 LLM 生成任务执行汇报"""
    from llm import chat

    # 构建执行结果摘要给 LLM
    results = []
    for st in task.subtasks:
        status_str = {"success": "成功", "failed": "失败", "skipped": "跳过"}.get(st.status, st.status)
        result_str = f"- Agent: {st.agent_id}\n  指令: {st.instruction}"
        if st.command and st.command != st.instruction:
            result_str += f"\n  执行命令: {st.command}"
        result_str += f"\n  状态: {status_str}"
        if st.output:
            result_str += f"\n  输出:\n{st.output[:2000]}"
        if st.error:
            result_str += f"\n  错误: {st.error}"
        results.append(result_str)

    prompt = f"""你是一个运维专家，请对以下多 Agent 协作任务的执行结果进行汇报分析。

用户目标：{task.goal}
整体状态：{task.status}

各子任务执行结果：
{chr(10).join(results)}

请用简洁清晰的中文生成一份执行汇报，要求：
1. 首先直接列出用户想要的核心数据/结果（如新闻标题列表、IP地址、端口列表等），不要省略
2. 简要说明任务完成情况
3. 如有失败，分析原因
4. 如有必要，给出后续建议

重要：用户最关心的是实际结果数据，请优先完整展示，不要用"输出内容符合预期"等模糊描述代替真实数据。"""

    try:
        return await chat([{"role": "user", "content": prompt}], max_tokens=800)
    except Exception as e:
        logger.error(f"AI 汇报生成失败: {e}")
        return _summarize(task)
