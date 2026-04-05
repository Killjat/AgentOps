"""Swarm API 路由"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from fastapi import APIRouter, HTTPException
from typing import List

from swarm_models import SwarmTaskRequest, SwarmTaskResponse
import coordinator

router = APIRouter(prefix="/swarm", tags=["swarm"])


@router.post("/tasks", response_model=SwarmTaskResponse)
async def create_swarm_task(req: SwarmTaskRequest):
    """创建并执行多 Agent 协作任务"""
    task = await coordinator.run_swarm(req)
    return SwarmTaskResponse(**task.dict())


@router.get("/tasks", response_model=List[SwarmTaskResponse])
async def list_swarm_tasks():
    return [SwarmTaskResponse(**t.dict()) for t in coordinator.list_tasks()]


@router.get("/tasks/{swarm_task_id}", response_model=SwarmTaskResponse)
async def get_swarm_task(swarm_task_id: str):
    task = coordinator.get_task(swarm_task_id)
    if not task:
        raise HTTPException(404, f"任务 {swarm_task_id} 不存在")
    return SwarmTaskResponse(**task.dict())
