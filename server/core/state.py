"""全局状态变量"""
import asyncio
from typing import Dict
from fastapi import WebSocket

from models import ServerInfo, AgentInfo, TaskResult, AppDeployResult

# ── 内存存储（可替换为数据库）────────────────────────────────────
servers: Dict[str, ServerInfo] = {}
agents: Dict[str, AgentInfo] = {}
tasks: Dict[str, TaskResult] = {}
app_deploys: Dict[str, AppDeployResult] = {}

# ── WebSocket 连接池 ─────────────────────────────────────────────
# agent_id → WebSocket 连接
_ws_connections: Dict[str, WebSocket] = {}
# agent_id → 待处理任务 {task_id → asyncio.Future}
_ws_pending: Dict[str, Dict[str, asyncio.Future]] = {}
