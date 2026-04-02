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
    name: str = ""               # 来自 hosts.yaml 的名称
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None  # 私钥路径（可选）
    deploy_dir: str = "/opt/agentops"


class AgentInfo(BaseModel):
    agent_id: str
    name: str = ""
    host: str
    port: int
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    os_type: OSType
    os_version: str
    deploy_dir: str
    status: AgentStatus = AgentStatus.OFFLINE
    agent_port: int = 9000
    created_at: str
    last_seen: Optional[str] = None
    metrics: Optional[dict] = None   # 最新上报的系统指标


class TaskRequest(BaseModel):
    task: str                          # 自然语言任务描述
    agent_id: str                      # 目标 Agent
    os_hint: Optional[str] = None      # 手动指定 OS（覆盖自动检测）
    auto_confirm: bool = True
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
