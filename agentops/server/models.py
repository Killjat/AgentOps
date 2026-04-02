"""数据模型"""
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class OSType(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    UNKNOWN = "unknown"


class AgentStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class LLMProvider(str, Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROK = "grok"


class RemoteHost(BaseModel):
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None  # 私钥路径（可选）
    deploy_dir: str = "/opt/agentops"


class AgentInfo(BaseModel):
    agent_id: str
    host: str
    port: int
    username: str
    password: Optional[str] = None   # 用于建 SSH 隧道
    ssh_key: Optional[str] = None    # 用于建 SSH 隧道（与 password 二选一）
    os_type: OSType
    os_version: str
    deploy_dir: str
    status: AgentStatus = AgentStatus.OFFLINE
    agent_port: int = 9000
    created_at: str
    last_seen: Optional[str] = None


class TaskRequest(BaseModel):
    task: str                          # 自然语言任务描述
    agent_id: str                      # 目标 Agent
    auto_confirm: bool = True          # 自动确认（不拦截危险命令提示）
    timeout: int = 60


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    status: TaskStatus
    task: str
    command: Optional[str] = None      # 生成的命令
    output: Optional[str] = None       # 执行输出
    analysis: Optional[str] = None     # AI 分析
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
