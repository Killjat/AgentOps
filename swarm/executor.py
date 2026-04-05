"""Swarm Executor - 并发调度子任务到各 Agent"""
import asyncio
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from datetime import datetime
from typing import Dict, Callable, Awaitable

from swarm_models import SwarmTask, SubTask, SubTaskStatus, SwarmTaskStatus

logger = logging.getLogger(__name__)


class SwarmExecutor:
    """
    并发执行 SwarmTask 中的所有子任务，支持依赖关系。
    dispatch_fn: async (agent_id, subtask) -> (success, output, error)
    """

    def __init__(self, dispatch_fn: Callable[[str, SubTask], Awaitable[tuple]]):
        self.dispatch = dispatch_fn

    async def run(self, task: SwarmTask) -> SwarmTask:
        """执行整个 swarm 任务，返回更新后的 task"""
        task.status = SwarmTaskStatus.RUNNING

        # subtask_id -> asyncio.Event，用于依赖等待
        done_events: Dict[str, asyncio.Event] = {
            st.subtask_id: asyncio.Event() for st in task.subtasks
        }

        async def run_subtask(subtask: SubTask):
            # 等待所有依赖完成
            for dep_id in subtask.depends_on:
                if dep_id in done_events:
                    await done_events[dep_id].wait()
                    # 如果依赖失败，跳过本任务
                    dep = next((s for s in task.subtasks if s.subtask_id == dep_id), None)
                    if dep and dep.status == SubTaskStatus.FAILED:
                        subtask.status = SubTaskStatus.SKIPPED
                        done_events[subtask.subtask_id].set()
                        return

            subtask.status = SubTaskStatus.RUNNING
            subtask.started_at = datetime.now().isoformat()

            try:
                success, output, error = await self.dispatch(subtask.agent_id, subtask)
                subtask.output = output
                subtask.error = error
                subtask.status = SubTaskStatus.SUCCESS if success else SubTaskStatus.FAILED
            except Exception as e:
                subtask.status = SubTaskStatus.FAILED
                subtask.error = str(e)
                logger.error(f"[swarm] subtask {subtask.subtask_id} 异常: {e}")
            finally:
                subtask.completed_at = datetime.now().isoformat()
                done_events[subtask.subtask_id].set()

        # 并发执行所有子任务（依赖关系由内部等待保证顺序）
        await asyncio.gather(*[run_subtask(st) for st in task.subtasks])

        # 汇总状态
        statuses = {st.status for st in task.subtasks}
        if all(s == SubTaskStatus.SUCCESS for s in [st.status for st in task.subtasks]):
            task.status = SwarmTaskStatus.SUCCESS
        elif all(s == SubTaskStatus.FAILED for s in [st.status for st in task.subtasks]):
            task.status = SwarmTaskStatus.FAILED
        else:
            task.status = SwarmTaskStatus.PARTIAL

        task.completed_at = datetime.now().isoformat()
        return task
