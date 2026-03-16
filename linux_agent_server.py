#!/usr/bin/env python3
"""
Linux 运维 Agent - Web 服务器版本
支持远程任务下发、实时状态监控、任务队列
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

# 导入本地 Agent 逻辑
import sys
sys.path.append('.')

SYSTEM_PROMPT = """你是一个 Linux 服务器运维专家助手。
用户会用自然语言描述需求，你需要生成对应的 Linux 命令。

规则：
1. 只返回可执行的命令，不要任何解释
2. 如果需要多个命令，用 && 连接
3. 优先使用安全的命令

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

# FastAPI 应用
app = FastAPI(
    title="Linux 运维 Agent API",
    description="远程任务下发和执行",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 任务状态
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

# 数据模型
class TaskRequest(BaseModel):
    task: str
    auto_confirm: bool = False
    dry_run: bool = False
    timeout: int = 30

class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    task: str
    command: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None

class AgentConfig(BaseModel):
    model_type: str = "ollama"
    model_name: str = "deepseek-r1:7b"
    base_url: Optional[str] = "http://localhost:11434"
    api_key: Optional[str] = None

# 全局状态
tasks: Dict[str, TaskResponse] = {}
agent_config = AgentConfig()
websocket_connections: List[WebSocket] = []

# Agent 核心逻辑
class AgentCore:
    """Agent 核心逻辑"""
    
    @staticmethod
    async def call_ollama(user_input: str) -> str:
        """调用 Ollama 生成命令"""
        try:
            import aiohttp
            
            url = f"{agent_config.base_url}/api/chat"
            data = {
                "model": agent_config.model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_input}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 200
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=60) as response:
                    result = await response.json()
                    command = result['message']['content'].strip()
                    
                    # 清理思考过程标记
                    if '<think>' in command:
                        command = re.sub(r'<think>.*?</think>', '', command, flags=re.DOTALL)
                    command = command.strip()
                    
                    return command
                    
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"调用模型失败: {str(e)}")
    
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
        """异步执行命令"""
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
async def broadcast_task_update(task_id: str):
    """广播任务更新到所有 WebSocket 连接"""
    if task_id in tasks:
        message = json.dumps({
            "type": "task_update",
            "data": tasks[task_id].dict()
        })
        
        disconnected = []
        for ws in websocket_connections:
            try:
                await ws.send_text(message)
            except:
                disconnected.append(ws)
        
        # 清理断开的连接
        for ws in disconnected:
            websocket_connections.remove(ws)

# API 端点
@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Linux 运维 Agent",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "POST /tasks": "提交新任务",
            "GET /tasks": "获取所有任务",
            "GET /tasks/{task_id}": "获取任务详情",
            "DELETE /tasks/{task_id}": "取消任务",
            "GET /config": "获取配置",
            "POST /config": "更新配置",
            "WebSocket /ws": "实时任务更新"
        }
    }

@app.post("/tasks", response_model=TaskResponse)
async def create_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """提交新任务"""
    task_id = str(uuid.uuid4())
    
    task = TaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        task=request.task,
        created_at=datetime.now().isoformat()
    )
    
    tasks[task_id] = task
    
    # 后台执行任务
    background_tasks.add_task(
        execute_task,
        task_id,
        request.task,
        request.auto_confirm,
        request.dry_run,
        request.timeout
    )
    
    return task

async def execute_task(task_id: str, user_input: str, auto_confirm: bool, dry_run: bool, timeout: int):
    """执行任务（后台）"""
    try:
        # 更新状态为运行中
        tasks[task_id].status = TaskStatus.RUNNING
        await broadcast_task_update(task_id)
        
        # 1. 生成命令
        command = await AgentCore.call_ollama(user_input)
        tasks[task_id].command = command
        await broadcast_task_update(task_id)
        
        if dry_run:
            tasks[task_id].status = TaskStatus.SUCCESS
            tasks[task_id].output = f"Dry-run 模式: {command}"
            tasks[task_id].completed_at = datetime.now().isoformat()
            await broadcast_task_update(task_id)
            return
        
        # 2. 安全检查
        is_safe, message = AgentCore.check_safety(command)
        
        if not is_safe and not auto_confirm:
            tasks[task_id].status = TaskStatus.FAILED
            tasks[task_id].error = f"安全检查失败: {message}。需要 auto_confirm=true"
            tasks[task_id].completed_at = datetime.now().isoformat()
            await broadcast_task_update(task_id)
            return
        
        # 3. 执行命令
        success, output = await AgentCore.execute_command(command, timeout)
        
        if success:
            tasks[task_id].status = TaskStatus.SUCCESS
            tasks[task_id].output = output
        else:
            tasks[task_id].status = TaskStatus.FAILED
            tasks[task_id].error = output
        
        tasks[task_id].completed_at = datetime.now().isoformat()
        await broadcast_task_update(task_id)
        
    except Exception as e:
        tasks[task_id].status = TaskStatus.FAILED
        tasks[task_id].error = str(e)
        tasks[task_id].completed_at = datetime.now().isoformat()
        await broadcast_task_update(task_id)

@app.get("/tasks", response_model=List[TaskResponse])
async def list_tasks(status: Optional[TaskStatus] = None, limit: int = 100):
    """获取任务列表"""
    task_list = list(tasks.values())
    
    if status:
        task_list = [t for t in task_list if t.status == status]
    
    # 按创建时间倒序
    task_list.sort(key=lambda x: x.created_at, reverse=True)
    
    return task_list[:limit]

@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """获取任务详情"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return tasks[task_id]

@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    """取消任务"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = tasks[task_id]
    
    if task.status in [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail="任务已完成，无法取消")
    
    task.status = TaskStatus.CANCELLED
    task.completed_at = datetime.now().isoformat()
    await broadcast_task_update(task_id)
    
    return {"message": "任务已取消", "task_id": task_id}

@app.get("/config", response_model=AgentConfig)
async def get_config():
    """获取配置"""
    return agent_config

@app.post("/config")
async def update_config(config: AgentConfig):
    """更新配置"""
    global agent_config
    agent_config = config
    return {"message": "配置已更新", "config": agent_config}

@app.get("/health")
async def health_check():
    """健康检查"""
    try:
        # 测试模型连接
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{agent_config.base_url}/api/tags", timeout=5) as response:
                model_status = "healthy" if response.status == 200 else "unhealthy"
    except:
        model_status = "unhealthy"
    
    return {
        "status": "healthy",
        "model_status": model_status,
        "model_type": agent_config.model_type,
        "model_name": agent_config.model_name,
        "total_tasks": len(tasks),
        "websocket_connections": len(websocket_connections)
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接 - 实时任务更新"""
    await websocket.accept()
    websocket_connections.append(websocket)
    
    try:
        # 发送欢迎消息
        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": "已连接到 Linux 运维 Agent",
            "total_tasks": len(tasks)
        }))
        
        # 保持连接
        while True:
            data = await websocket.receive_text()
            # 可以处理客户端发来的消息
            
    except WebSocketDisconnect:
        websocket_connections.remove(websocket)

@app.on_event("startup")
async def startup_event():
    """启动事件"""
    print("=" * 60)
    print("🚀 Linux 运维 Agent 服务器启动")
    print("=" * 60)
    print(f"📡 API 文档: http://localhost:8000/docs")
    print(f"🔧 模型: {agent_config.model_type} ({agent_config.model_name})")
    print(f"🌐 WebSocket: ws://localhost:8000/ws")
    print("=" * 60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
