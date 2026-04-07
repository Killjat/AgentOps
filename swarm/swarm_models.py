"""Swarm 数据模型"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from enum import Enum


class SwarmTaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"      # LLM 正在拆解任务
    RUNNING = "running"        # 子任务执行中
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"        # 部分成功


class SubTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class SubTask(BaseModel):
    """分配给单个 Agent 的子任务"""
    subtask_id: str
    swarm_task_id: str
    agent_id: str
    instruction: str           # 自然语言指令
    command: Optional[str] = None
    depends_on: List[str] = [] # 依赖的 subtask_id 列表
    status: SubTaskStatus = SubTaskStatus.PENDING
    output: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class SwarmTask(BaseModel):
    """跨多 Agent 的协作任务"""
    swarm_task_id: str
    owner: str = ""
    goal: str                  # 用户原始目标
    agent_ids: List[str]       # 参与的 Agent 列表
    subtasks: List[SubTask] = []
    status: SwarmTaskStatus = SwarmTaskStatus.PENDING
    plan: Optional[str] = None # LLM 生成的执行计划
    summary: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class SwarmTaskRequest(BaseModel):
    goal: str
    agent_ids: List[str]       # 指定参与的 Agent
    auto_confirm: bool = True
    timeout: int = 300
    context: Optional[str] = None  # 追问时带上上一次任务的摘要


class SwarmTaskResponse(BaseModel):
    swarm_task_id: str
    status: SwarmTaskStatus
    goal: str
    agent_ids: List[str]
    subtasks: List[SubTask] = []
    plan: Optional[str] = None
    summary: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
