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
    SSH = "ssh"           # Linux/macOS SSH
    RDP = "rdp"           # Windows 远程桌面
    USB = "usb"           # USB 直连（手机/设备）
    AGENT_PUSH = "agent_push"  # Agent 主动上报（内网穿透）
    API = "api"           # HTTP API 接入


class DeviceType(str, Enum):
    SERVER = "server"           # 服务器
    DESKTOP = "desktop"         # 桌面电脑
    MOBILE_ANDROID = "mobile_android"  # Android 手机
    MOBILE_IOS = "mobile_ios"   # iOS 手机
    IOT = "iot"                 # IoT 设备
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
    name: str = ""
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    deploy_dir: str = "/opt/agentops"


class AgentInfo(BaseModel):
    agent_id: str
    name: str = ""
    owner: str = ""
    # 分类
    os_type: OSType = OSType.UNKNOWN
    os_version: str = ""
    device_type: DeviceType = DeviceType.SERVER
    connection_type: ConnectionType = ConnectionType.SSH
    # 连接信息
    host: str = ""
    port: int = 22
    username: str = ""
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    deploy_dir: str = "/opt/agentops"
    # 状态
    status: AgentStatus = AgentStatus.OFFLINE
    agent_port: int = 9000
    created_at: str
    last_seen: Optional[str] = None
    metrics: Optional[dict] = None


class TaskRequest(BaseModel):
    task: str                          # 自然语言任务描述
    agent_id: str                      # 目标 Agent
    os_hint: Optional[str] = None      # 手动指定 OS（覆盖自动检测）
    auto_confirm: bool = True
    timeout: int = 60


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    owner: str = ""              # 提交任务的用户
    status: TaskStatus
    task: str
    command: Optional[str] = None
    output: Optional[str] = None
    analysis: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    conversation: List[dict] = []      # 后续对话记录


class ChatRequest(BaseModel):
    task_id: str                       # 基于哪个任务继续对话
    message: str                       # 用户消息
    execute: bool = False              # 是否执行 AI 生成的命令


class AppDeployStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class AppDeployRequest(BaseModel):
    agent_id: str                        # 目标服务器
    repo_url: str                        # GitHub 仓库地址
    branch: str = "main"                 # 分支
    deploy_dir: str = "/opt/agentops"         # 部署到目标服务器的目录
    install_cmd: str = ""                # 安装依赖命令，如 pip install -r requirements.txt
    start_cmd: str = ""                  # 启动命令，如 python3 app.py
    use_systemd: bool = False            # 是否注册为 systemd 服务
    service_name: str = ""               # systemd 服务名


class AppDeployResult(BaseModel):
    deploy_id: str
    agent_id: str
    owner: str = ""
    repo_url: str
    deploy_dir: str
    status: AppDeployStatus = AppDeployStatus.PENDING
    log: str = ""
    conversation: List[dict] = []
    created_at: str
    completed_at: Optional[str] = None
