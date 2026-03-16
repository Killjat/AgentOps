#!/usr/bin/env python3
"""
Linux 运维 Agent - 多 Agent 管理系统
支持多个 Agent，每个 Agent 有独立的编号、角色、配置
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import subprocess
import asyncio
import json
import uuid
import re
from datetime import datetime
from enum import Enum

app = FastAPI(
    title="AgentOps API",
    description="AI-Powered Operations System",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Agent 角色定义
class AgentRole(str, Enum):
    MONITOR = "monitor"          # 监控专家
    SECURITY = "security"        # 安全专家
    NETWORK = "network"          # 网络专家
    DATABASE = "database"        # 数据库专家
    DEVOPS = "devops"           # DevOps 专家
    GENERAL = "general"         # 通用运维

# 任务状态
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

# Agent 状态
class AgentStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"

# 数据模型
class LLMProvider(str, Enum):
    DEEPSEEK = "deepseek"
    GROK = "grok"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"

class Agent(BaseModel):
    agent_id: str
    name: str
    role: AgentRole
    status: AgentStatus = AgentStatus.ONLINE
    description: Optional[str] = None
    llm_provider: LLMProvider = LLMProvider.DEEPSEEK
    model_name: str = "deepseek-chat"
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    max_concurrent_tasks: int = 3
    current_tasks: int = 0
    total_tasks_completed: int = 0
    created_at: str
    last_active: str
    tags: List[str] = []
    capabilities: List[str] = []

class TaskRequest(BaseModel):
    task: str
    agent_id: Optional[str] = None  # 指定 Agent，不指定则自动分配
    role: Optional[AgentRole] = None  # 指定角色，自动选择该角色的 Agent
    auto_confirm: bool = False
    dry_run: bool = False
    timeout: int = 30
    priority: int = 0  # 优先级 0-10

class Task(BaseModel):
    task_id: str
    agent_id: str
    status: TaskStatus
    task: str
    command: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    analysis: Optional[str] = None  # 大模型对结果的分析
    priority: int = 0
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

# 全局状态
agents: Dict[str, Agent] = {}
tasks: Dict[str, Task] = {}
websocket_connections: List[WebSocket] = []

# Agent 角色的专长提示词
ROLE_PROMPTS = {
    AgentRole.MONITOR: """你是一个 Linux 系统监控专家。
专长：系统性能监控、资源使用分析、进程管理、日志分析。
优先使用：top, htop, ps, free, df, iostat, vmstat, sar 等监控工具。""",
    
    AgentRole.SECURITY: """你是一个 Linux 安全专家。
专长：安全审计、权限管理、防火墙配置、入侵检测。
优先使用：iptables, ufw, fail2ban, last, who, grep auth.log 等安全工具。""",
    
    AgentRole.NETWORK: """你是一个 Linux 网络专家。
专长：网络诊断、连接管理、端口监控、流量分析。
优先使用：ss, netstat, tcpdump, ping, traceroute, nmap 等网络工具。""",
    
    AgentRole.DATABASE: """你是一个数据库运维专家。
专长：数据库管理、备份恢复、性能优化、查询分析。
优先使用：mysql, psql, mongosh, redis-cli 等数据库工具。""",
    
    AgentRole.DEVOPS: """你是一个 DevOps 专家。
专长：容器管理、CI/CD、服务部署、自动化运维。
优先使用：docker, kubectl, systemctl, git, ansible 等 DevOps 工具。""",
    
    AgentRole.GENERAL: """你是一个通用 Linux 运维专家。
专长：全面的系统管理和问题排查。
可以处理各类运维任务。"""
}

SYSTEM_PROMPT_TEMPLATE = """{role_prompt}

用户会用自然语言描述需求，你需要生成对应的 Linux 命令。

规则：
1. 只返回可执行的命令，不要任何解释
2. 如果需要多个命令，用 && 连接
3. 优先使用安全的命令
4. 根据你的专长选择最合适的工具

示例：
用户：查看系统开放端口
助手：ss -tuln | grep LISTEN
"""

DANGEROUS_PATTERNS = [
    r'rm\s+-rf\s+/',
    r'dd\s+if=/dev/zero',
    r'mkfs\.',
]

CONFIRM_COMMANDS = ['rm', 'kill', 'reboot', 'shutdown']

# LLM API 配置
LLM_API_CONFIGS = {
    LLMProvider.DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat"
    },
    LLMProvider.GROK: {
        "base_url": "https://api.x.ai/v1/chat/completions",
        "default_model": "grok-beta"
    },
    LLMProvider.OPENAI: {
        "base_url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4"
    },
    LLMProvider.ANTHROPIC: {
        "base_url": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-3-5-sonnet-20241022"
    }
}

# 全局 API Keys（可以通过环境变量或配置文件设置）
import os
GLOBAL_API_KEYS = {
    LLMProvider.DEEPSEEK: os.getenv("DEEPSEEK_API_KEY"),
    LLMProvider.GROK: os.getenv("GROK_API_KEY"),
    LLMProvider.OPENAI: os.getenv("OPENAI_API_KEY"),
    LLMProvider.ANTHROPIC: os.getenv("ANTHROPIC_API_KEY")
}


# Agent 管理
class AgentManager:
    """Agent 管理器"""
    
    @staticmethod
    def create_agent(name: str, role: AgentRole, description: str = None, 
                    llm_provider: LLMProvider = LLMProvider.DEEPSEEK,
                    model_name: str = None, api_key: str = None,
                    tags: List[str] = None) -> Agent:
        """创建新 Agent"""
        agent_id = f"agent-{len(agents) + 1:03d}"
        
        # 根据角色设置能力
        capabilities = {
            AgentRole.MONITOR: ["系统监控", "性能分析", "资源管理", "日志分析"],
            AgentRole.SECURITY: ["安全审计", "权限管理", "防火墙配置", "入侵检测"],
            AgentRole.NETWORK: ["网络诊断", "端口扫描", "流量分析", "连接管理"],
            AgentRole.DATABASE: ["数据库管理", "备份恢复", "性能优化", "查询分析"],
            AgentRole.DEVOPS: ["容器管理", "CI/CD", "服务部署", "自动化运维"],
            AgentRole.GENERAL: ["通用运维", "问题排查", "系统管理"]
        }.get(role, [])
        
        # 使用默认模型名称
        if not model_name:
            model_name = LLM_API_CONFIGS[llm_provider]["default_model"]
        
        # 使用全局 API Key 或指定的 API Key
        if not api_key:
            api_key = GLOBAL_API_KEYS.get(llm_provider)
        
        agent = Agent(
            agent_id=agent_id,
            name=name,
            role=role,
            description=description or f"{role.value} 专家",
            llm_provider=llm_provider,
            model_name=model_name,
            api_key=api_key,
            api_base_url=LLM_API_CONFIGS[llm_provider]["base_url"],
            capabilities=capabilities,
            tags=tags or [],
            created_at=datetime.now().isoformat(),
            last_active=datetime.now().isoformat()
        )
        
        agents[agent_id] = agent
        return agent
    
    @staticmethod
    def select_agent(task_request: TaskRequest) -> Optional[Agent]:
        """选择合适的 Agent 执行任务"""
        # 1. 如果指定了 agent_id，直接返回
        if task_request.agent_id:
            return agents.get(task_request.agent_id)
        
        # 2. 如果指定了角色，选择该角色的可用 Agent
        available_agents = [
            a for a in agents.values()
            if a.status == AgentStatus.ONLINE
            and a.current_tasks < a.max_concurrent_tasks
        ]
        
        if task_request.role:
            available_agents = [
                a for a in available_agents
                if a.role == task_request.role
            ]
        
        if not available_agents:
            return None
        
        # 3. 选择当前任务最少的 Agent
        return min(available_agents, key=lambda a: a.current_tasks)
    
    @staticmethod
    async def call_llm_api(agent: Agent, user_input: str) -> str:
        """调用 LLM API 生成命令"""
        try:
            import aiohttp
            import ssl
            
            # 创建 SSL context（跳过证书验证，仅用于开发测试）
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # 根据 Agent 角色构建 Prompt
            role_prompt = ROLE_PROMPTS.get(agent.role, ROLE_PROMPTS[AgentRole.GENERAL])
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(role_prompt=role_prompt)
            
            # 检查 API Key
            if not agent.api_key:
                raise HTTPException(
                    status_code=500, 
                    detail=f"Agent {agent.agent_id} 未配置 API Key"
                )
            
            # 根据不同的 LLM Provider 构建请求
            if agent.llm_provider == LLMProvider.ANTHROPIC:
                # Anthropic 使用不同的 API 格式
                headers = {
                    "x-api-key": agent.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }
                data = {
                    "model": agent.model_name,
                    "max_tokens": 200,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_input}
                    ]
                }
            else:
                # OpenAI 兼容格式（DeepSeek, Grok, OpenAI）
                headers = {
                    "Authorization": f"Bearer {agent.api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": agent.model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_input}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200
                }
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    agent.api_base_url, 
                    headers=headers, 
                    json=data, 
                    timeout=60
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise HTTPException(
                            status_code=500,
                            detail=f"LLM API 调用失败 ({response.status}): {error_text}"
                        )
                    
                    result = await response.json()
                    
                    # 解析响应
                    if agent.llm_provider == LLMProvider.ANTHROPIC:
                        command = result['content'][0]['text'].strip()
                    else:
                        command = result['choices'][0]['message']['content'].strip()
                    
                    # 清理思考过程标记（DeepSeek R1 可能有）
                    if '<think>' in command:
                        command = re.sub(r'<think>.*?</think>', '', command, flags=re.DOTALL)
                    command = command.strip()
                    
                    return command
                    
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"调用 LLM API 失败: {str(e)}")
    
    @staticmethod
    async def analyze_result(agent: Agent, task: str, command: str, output: str, success: bool) -> str:
        """让大模型分析执行结果"""
        try:
            import aiohttp
            import ssl
            
            # 创建 SSL context
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # 构建分析 Prompt
            analysis_prompt = f"""你是一个 Linux 运维专家。用户刚刚执行了一个任务，请分析执行结果。

任务描述: {task}
执行的命令: {command}
执行状态: {'成功' if success else '失败'}

执行结果:
{output[:1000]}  # 限制长度

请简要分析:
1. 结果是否符合预期？
2. 是否发现任何问题或异常？
3. 如果有问题，给出建议的解决方案
4. 如果成功，总结关键信息

请用简洁的中文回答（不超过200字）。"""

            if not agent.api_key:
                return "无法分析：Agent 未配置 API Key"
            
            # 构建请求
            if agent.llm_provider == LLMProvider.ANTHROPIC:
                headers = {
                    "x-api-key": agent.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }
                data = {
                    "model": agent.model_name,
                    "max_tokens": 300,
                    "messages": [
                        {"role": "user", "content": analysis_prompt}
                    ]
                }
            else:
                headers = {
                    "Authorization": f"Bearer {agent.api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": agent.model_name,
                    "messages": [
                        {"role": "user", "content": analysis_prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 300
                }
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    agent.api_base_url, 
                    headers=headers, 
                    json=data, 
                    timeout=30
                ) as response:
                    if response.status != 200:
                        return f"分析失败：API 返回 {response.status}"
                    
                    result = await response.json()
                    
                    # 解析响应
                    if agent.llm_provider == LLMProvider.ANTHROPIC:
                        analysis = result['content'][0]['text'].strip()
                    else:
                        analysis = result['choices'][0]['message']['content'].strip()
                    
                    return analysis
                    
        except Exception as e:
            return f"分析失败: {str(e)}"
    
    @staticmethod
    def check_safety(command: str) -> tuple[bool, str]:
        """安全检查"""
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                return False, f"危险命令被拦截"
        
        for cmd in CONFIRM_COMMANDS:
            if command.split()[0] == cmd:
                return False, f"需要用户确认"
        
        return True, "安全检查通过"
    
    @staticmethod
    async def execute_command(command: str, timeout: int = 30) -> tuple[bool, str]:
        """执行命令"""
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            
            output = stdout.decode() if stdout else stderr.decode()
            success = process.returncode == 0
            
            return success, output
            
        except asyncio.TimeoutError:
            return False, f"命令执行超时（{timeout}秒）"
        except Exception as e:
            return False, f"执行失败: {str(e)}"

# WebSocket 广播
async def broadcast_message(message: dict):
    """广播消息到所有 WebSocket 连接"""
    message_str = json.dumps(message)
    
    disconnected = []
    for ws in websocket_connections:
        try:
            await ws.send_text(message_str)
        except:
            disconnected.append(ws)
    
    for ws in disconnected:
        websocket_connections.remove(ws)

# API 端点
@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "AgentOps",
        "description": "AI-Powered Operations System",
        "version": "2.0.0",
        "total_agents": len(agents),
        "total_tasks": len(tasks),
        "endpoints": {
            "POST /agents": "创建 Agent",
            "GET /agents": "获取所有 Agent",
            "GET /agents/{agent_id}": "获取 Agent 详情",
            "DELETE /agents/{agent_id}": "删除 Agent",
            "POST /tasks": "提交任务",
            "GET /tasks": "获取所有任务",
            "GET /tasks/{task_id}": "获取任务详情",
            "WebSocket /ws": "实时更新"
        }
    }

@app.post("/agents", response_model=Agent)
async def create_agent(
    name: str,
    role: AgentRole,
    llm_provider: LLMProvider = LLMProvider.DEEPSEEK,
    description: Optional[str] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    tags: Optional[List[str]] = None
):
    """创建新 Agent"""
    agent = AgentManager.create_agent(
        name, role, description, llm_provider, model_name, api_key, tags
    )
    
    await broadcast_message({
        "type": "agent_created",
        "data": agent.dict()
    })
    
    return agent

@app.get("/agents", response_model=List[Agent])
async def list_agents(role: Optional[AgentRole] = None, status: Optional[AgentStatus] = None):
    """获取 Agent 列表"""
    agent_list = list(agents.values())
    
    if role:
        agent_list = [a for a in agent_list if a.role == role]
    
    if status:
        agent_list = [a for a in agent_list if a.status == status]
    
    return agent_list

@app.get("/agents/{agent_id}", response_model=Agent)
async def get_agent(agent_id: str):
    """获取 Agent 详情"""
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    return agents[agent_id]

@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """删除 Agent"""
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    agent = agents[agent_id]
    
    if agent.current_tasks > 0:
        raise HTTPException(status_code=400, detail="Agent 正在执行任务，无法删除")
    
    del agents[agent_id]
    
    await broadcast_message({
        "type": "agent_deleted",
        "agent_id": agent_id
    })
    
    return {"message": "Agent 已删除", "agent_id": agent_id}

@app.post("/tasks", response_model=Task)
async def create_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """提交新任务"""
    # 选择 Agent
    agent = AgentManager.select_agent(request)
    
    if not agent:
        raise HTTPException(status_code=503, detail="没有可用的 Agent")
    
    task_id = str(uuid.uuid4())
    
    task = Task(
        task_id=task_id,
        agent_id=agent.agent_id,
        status=TaskStatus.PENDING,
        task=request.task,
        priority=request.priority,
        created_at=datetime.now().isoformat()
    )
    
    tasks[task_id] = task
    agent.current_tasks += 1
    
    # 后台执行任务
    background_tasks.add_task(
        execute_task,
        task_id,
        agent.agent_id,
        request.task,
        request.auto_confirm,
        request.dry_run,
        request.timeout
    )
    
    return task

async def execute_task(task_id: str, agent_id: str, user_input: str, 
                      auto_confirm: bool, dry_run: bool, timeout: int):
    """执行任务"""
    agent = agents[agent_id]
    task = tasks[task_id]
    
    try:
        # 更新状态
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        agent.status = AgentStatus.BUSY
        agent.last_active = datetime.now().isoformat()
        
        await broadcast_message({
            "type": "task_update",
            "data": task.dict()
        })
        
        # 生成命令
        command = await AgentManager.call_llm_api(agent, user_input)
        task.command = command
        
        await broadcast_message({
            "type": "task_update",
            "data": task.dict()
        })
        
        if dry_run:
            task.status = TaskStatus.SUCCESS
            task.output = f"Dry-run 模式: {command}"
            task.completed_at = datetime.now().isoformat()
            agent.current_tasks -= 1
            agent.total_tasks_completed += 1
            agent.status = AgentStatus.ONLINE if agent.current_tasks == 0 else AgentStatus.BUSY
            await broadcast_message({"type": "task_update", "data": task.dict()})
            return
        
        # 安全检查
        is_safe, message = AgentManager.check_safety(command)
        
        if not is_safe and not auto_confirm:
            task.status = TaskStatus.FAILED
            task.error = f"安全检查失败: {message}"
            task.completed_at = datetime.now().isoformat()
            agent.current_tasks -= 1
            agent.status = AgentStatus.ONLINE if agent.current_tasks == 0 else AgentStatus.BUSY
            await broadcast_message({"type": "task_update", "data": task.dict()})
            return
        
        # 执行命令
        success, output = await AgentManager.execute_command(command, timeout)
        
        if success:
            task.status = TaskStatus.SUCCESS
            task.output = output
        else:
            task.status = TaskStatus.FAILED
            task.error = output
        
        task.completed_at = datetime.now().isoformat()
        
        # 让大模型分析结果
        print(f"🤔 Agent {agent.agent_id} 正在分析结果...")
        analysis = await AgentManager.analyze_result(
            agent, 
            user_input, 
            command, 
            output if success else task.error,
            success
        )
        task.analysis = analysis
        
        agent.current_tasks -= 1
        agent.total_tasks_completed += 1
        agent.status = AgentStatus.ONLINE if agent.current_tasks == 0 else AgentStatus.BUSY
        agent.last_active = datetime.now().isoformat()
        
        await broadcast_message({
            "type": "task_update",
            "data": task.dict()
        })
        
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.completed_at = datetime.now().isoformat()
        agent.current_tasks -= 1
        agent.status = AgentStatus.ERROR
        await broadcast_message({"type": "task_update", "data": task.dict()})

@app.get("/tasks", response_model=List[Task])
async def list_tasks(
    agent_id: Optional[str] = None,
    status: Optional[TaskStatus] = None,
    limit: int = 100
):
    """获取任务列表"""
    task_list = list(tasks.values())
    
    if agent_id:
        task_list = [t for t in task_list if t.agent_id == agent_id]
    
    if status:
        task_list = [t for t in task_list if t.status == status]
    
    task_list.sort(key=lambda x: (x.priority, x.created_at), reverse=True)
    
    return task_list[:limit]

@app.get("/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str):
    """获取任务详情"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return tasks[task_id]

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接"""
    await websocket.accept()
    websocket_connections.append(websocket)
    
    try:
        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": "已连接到 Multi-Agent 系统",
            "total_agents": len(agents),
            "total_tasks": len(tasks)
        }))
        
        while True:
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        websocket_connections.remove(websocket)

@app.on_event("startup")
async def startup_event():
    """启动事件 - 创建默认 Agent"""
    print("=" * 60)
    print("🚀 AgentOps 系统启动")
    print("   AI-Powered Operations, Simplified")
    print("=" * 60)
    
    # 检查是否配置了 API Key
    if not GLOBAL_API_KEYS.get(LLMProvider.DEEPSEEK):
        print("⚠️  警告: 未配置 DEEPSEEK_API_KEY 环境变量")
        print("   请设置: export DEEPSEEK_API_KEY='your-api-key'")
        print("   或在创建 Agent 时手动指定 API Key")
    
    # 创建默认 Agent（如果配置了 API Key）
    if GLOBAL_API_KEYS.get(LLMProvider.DEEPSEEK):
        AgentManager.create_agent("监控专家-01", AgentRole.MONITOR, "系统监控和性能分析")
        AgentManager.create_agent("安全专家-01", AgentRole.SECURITY, "安全审计和权限管理")
        AgentManager.create_agent("网络专家-01", AgentRole.NETWORK, "网络诊断和连接管理")
        AgentManager.create_agent("通用运维-01", AgentRole.GENERAL, "通用运维任务")
        print(f"✅ 已创建 {len(agents)} 个默认 Agent (使用 DeepSeek)")
    else:
        print("ℹ️  未创建默认 Agent，请手动创建并指定 API Key")
    
    print(f"📡 API 文档: http://localhost:8000/docs")
    print(f"🌐 WebSocket: ws://localhost:8000/ws")
    print("=" * 60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
