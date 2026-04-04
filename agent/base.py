"""BaseAgent - WebSocket 反向连接架构"""
import abc
import asyncio
import json
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Dict, List, Any, Optional

# ── 安全黑名单 ──────────────────────────────────────────────
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/[^/]",
    r"dd\s+if=/dev/zero",
    r"mkfs\.",
    r":\(\)\{.*\}",
    r">\s*/dev/sd[a-z]",
    r"format\s+c:",
    r"del\s+/[sf].*\\",
]


def is_safe(command: str) -> tuple:
    for p in DANGEROUS_PATTERNS:
        if re.search(p, command, re.IGNORECASE):
            return False, f"危险命令被拦截: {p}"
    return True, "ok"


class BaseAgent(abc.ABC):

    def __init__(self, agent_id: str = "", server_url: str = "",
                 port: int = 9000, host: str = "0.0.0.0"):
        self.agent_id = agent_id
        self.server_url = server_url.rstrip("/")
        self.port = port
        self.host = host
        self._ws = None  # 当前 WebSocket 连接

    # ── 子类必须实现 ──────────────────────────────────────────

    @abc.abstractmethod
    def get_os_info(self) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def get_cpu_usage(self) -> float: ...

    @abc.abstractmethod
    def get_disk_usage(self) -> List[Dict[str, Any]]: ...

    @abc.abstractmethod
    def get_network_ips(self) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def get_network_io(self) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def get_hardware_info(self) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def execute_command(self, command: str, timeout: int = 60) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def discover_apps(self) -> Dict[str, Any]: ...

    # ── 通用指标采集 ──────────────────────────────────────────

    def collect_metrics(self) -> dict:
        return {
            "timestamp": datetime.now().isoformat(),
            "agent_id": self.agent_id,
            "os_info": self.get_os_info(),
            "cpu_usage": self.get_cpu_usage(),
            "disk": self.get_disk_usage(),
            "network": self.get_network_ips(),
            "network_io": self.get_network_io(),
            "hardware": self.get_hardware_info(),
        }

    # ── 消息处理 ──────────────────────────────────────────────

    async def _handle_message(self, ws, msg: dict) -> Optional[dict]:
        """处理 server 下发的消息，返回响应"""
        msg_type = msg.get("type")
        task_id = msg.get("task_id", "")

        if msg_type == "ping":
            return {"type": "pong", "task_id": task_id}

        elif msg_type == "exec":
            command = msg.get("command", "").strip()
            timeout = int(msg.get("timeout", 60))
            if not command:
                return {"type": "result", "task_id": task_id,
                        "success": False, "output": "", "error": "command is required", "done": True}
            safe, reason = is_safe(command)
            if not safe:
                return {"type": "result", "task_id": task_id,
                        "success": False, "output": "", "error": reason, "done": True}
            # 在线程池里执行，避免阻塞事件循环
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self.execute_command(command, timeout))
            return {"type": "result", "task_id": task_id, "done": True, **result}

        elif msg_type == "metrics":
            loop = asyncio.get_event_loop()
            metrics = await loop.run_in_executor(None, self.collect_metrics)
            return {"type": "metrics_result", "task_id": task_id, "metrics": metrics, "done": True}

        elif msg_type == "discover":
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.discover_apps)
            return {"type": "discover_result", "task_id": task_id, "data": result, "done": True}

        else:
            return {"type": "error", "task_id": task_id, "error": f"unknown type: {msg_type}"}

    # ── WebSocket 连接循环 ────────────────────────────────────

    async def _ws_loop(self):
        """WebSocket 主循环，带指数退避重连"""
        import websockets

        ws_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/agent/{self.agent_id}"

        delay = 1
        while True:
            try:
                print(f"[{_ts()}] 连接 {ws_url} ...")
                import ssl as _ssl
                ssl_ctx = _ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                    ssl=ssl_ctx if ws_url.startswith("wss://") else None,
                ) as ws:
                    self._ws = ws
                    delay = 1  # 连上了，重置退避
                    print(f"[{_ts()}] OK connected, Agent ID: {self.agent_id}")

                    # 注册
                    await ws.send(json.dumps({
                        "type": "register",
                        "agent_id": self.agent_id,
                        "os_info": self.get_os_info(),
                    }))

                    # 消息循环 — 每条消息独立 task，不阻塞后续消息
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            # 兼容 Python 3.6（无 asyncio.create_task）
                            asyncio.ensure_future(self._dispatch(ws, msg))
                        except Exception as e:
                            print(f"[{_ts()}] 消息解析错误: {e}")

            except Exception as e:
                print(f"[{_ts()}] 连接断开: {e}，{delay}s 后重连...")
            finally:
                self._ws = None

            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _dispatch(self, ws, msg: dict):
        """独立协程处理单条消息，异常不影响主循环"""
        try:
            resp = await self._handle_message(ws, msg)
            if resp:
                await ws.send(json.dumps(resp))
        except Exception as e:
            task_id = msg.get("task_id", "")
            print(f"[{_ts()}] 任务 {task_id} 处理失败: {e}")
            try:
                await ws.send(json.dumps({
                    "type": "result", "task_id": task_id,
                    "success": False, "output": "", "error": str(e), "done": True
                }))
            except Exception:
                pass

    # ── 启动 ──────────────────────────────────────────────────

    def start(self):
        info = self.get_os_info()
        print(f"CyberAgentOps Agent 启动")
        print(f"  类型: {self.__class__.__name__}")
        print(f"  系统: {info.get('os')} {info.get('os_version', '')[:40]}")
        print(f"  主机: {info.get('hostname', socket.gethostname())}")
        print(f"  Agent ID: {self.agent_id}")
        if self.server_url:
            print(f"  Server: {self.server_url}")
            import sys
            if sys.platform == 'win32':
                # Windows 需要 ProactorEventLoop 支持 WebSocket
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._ws_loop())
            finally:
                loop.close()
        else:
            print("  [!] 未配置 server_url，仅本地运行")
            # 没有 server 时阻塞等待（保持进程存活）
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                print("Agent 已停止")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")
