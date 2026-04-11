"""数据模型"""
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class OSType(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    ANDROID = "android"
    IOS = "ios"
    UNKNOWN = "unknown"


class ConnectionType(str, Enum):
    SSH = "ssh"
    RDP = "rdp"
    USB = "usb"
    AGENT_PUSH = "agent_push"
    API = "api"


class DeviceType(str, Enum):
    SERVER = "server"
    DESKTOP = "desktop"
    MOBILE_ANDROID = "mobile_android"
    MOBILE_IOS = "mobile_ios"
    IOT = "iot"
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
    """SSH 连接信息，用于 deployer"""
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    deploy_dir: Optional[str] = None


class ServerInfo(BaseModel):
    """服务器连接信息"""
    server_id: str
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    os_type: OSType = OSType.UNKNOWN
    os_version: str = ""
    owner: str = ""
    created_at: str = ""
    last_connected: Optional[str] = None


class AgentInfo(BaseModel):
    """已部署的 Agent"""
    agent_id: str
    server_id: str = ""
    name: str = ""
    owner: str = ""
    os_type: OSType = OSType.UNKNOWN
    os_version: str = ""
    device_type: DeviceType = DeviceType.SERVER
    connection_type: ConnectionType = ConnectionType.SSH
    agent_deploy_dir: str = "/opt/agentops"
    agent_port: int = 9000
    status: AgentStatus = AgentStatus.OFFLINE
    created_at: str
    last_seen: Optional[str] = None
    metrics: Optional[dict] = None
    agent_token: Optional[str] = None  # 用户自安装时的关联 token


class TaskRequest(BaseModel):
    task: str
    agent_id: str
    os_hint: Optional[str] = None
    auto_confirm: bool = True
    timeout: int = 60


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    owner: str = ""
    status: TaskStatus
    task: str
    command: Optional[str] = None
    output: Optional[str] = None
    analysis: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    conversation: List[dict] = []


class ChatRequest(BaseModel):
    task_id: str
    message: str
    execute: bool = False


class AppDeployStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class AppDeployRequest(BaseModel):
    agent_id: str                      # 目标 Agent ID
    repo_url: str
    branch: str = "main"
    deploy_dir: str = "/opt/app"       # 应用部署目录
    install_cmd: str = ""
    start_cmd: str = ""
    use_systemd: bool = False
    service_name: str = ""


class AppDeployResult(BaseModel):
    deploy_id: str
    agent_id: str
    owner: str = ""
    repo_url: str
    deploy_dir: str = "/opt/app"
    status: AppDeployStatus = AppDeployStatus.PENDING
    log: str = ""
    conversation: List[dict] = []
    created_at: str
    completed_at: Optional[str] = None
