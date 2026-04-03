"""BaseAgent - 所有 Agent 的抽象基类"""
import abc
import hashlib
import json
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from urllib.request import urlopen, Request

# ── 安全黑名单 ──────────────────────────────────────────────
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/[^/]",
    r"dd\s+if=/dev/zero",
    r"mkfs\.",
    r":\(\)\{.*\}",
    r">\s*/dev/sd[a-z]",
    r"format\s+c:",          # Windows
    r"del\s+/[sf].*\\",      # Windows
]


def is_safe(command: str) -> tuple:
    for p in DANGEROUS_PATTERNS:
        if re.search(p, command, re.IGNORECASE):
            return False, f"危险命令被拦截: {p}"
    return True, "ok"


class BaseAgent(abc.ABC):
    """所有 Agent 的基类，定义统一接口"""

    def __init__(self, agent_id: str = "", server_url: str = "",
                 port: int = 9000, host: str = "0.0.0.0"):
        self.agent_id = agent_id
        self.server_url = server_url.rstrip("/")
        self.port = port
        self.host = host
        self.report_interval = 600  # 10分钟

    # ── 必须实现的接口 ────────────────────────────────────────

    @abc.abstractmethod
    def get_os_info(self) -> dict:
        """返回操作系统信息"""

    @abc.abstractmethod
    def get_cpu_usage(self) -> float:
        """返回 CPU 使用率（0-100）"""

    @abc.abstractmethod
    def get_disk_usage(self) -> list:
        """返回磁盘使用情况列表"""

    @abc.abstractmethod
    def get_network_ips(self) -> dict:
        """返回网络 IP 信息"""

    @abc.abstractmethod
    def get_network_io(self) -> dict:
        """返回网络 IO 速率"""

    @abc.abstractmethod
    def get_hardware_info(self) -> dict:
        """返回硬件信息（CPU型号、主板、磁盘ID、MAC地址等）"""

    @abc.abstractmethod
    def execute_command(self, command: str, timeout: int = 60) -> dict:
        """执行命令，返回 {success, output, error}"""

    # ── 通用实现 ──────────────────────────────────────────────

    def collect_metrics(self) -> dict:
        """采集完整系统指标（通用，子类可覆盖）"""
        hw = self.get_hardware_info()
        return {
            "timestamp": datetime.now().isoformat(),
            "agent_id": self.agent_id,
            "os_info": self.get_os_info(),
            "cpu_usage": self.get_cpu_usage(),
            "disk": self.get_disk_usage(),
            "network": self.get_network_ips(),
            "network_io": self.get_network_io(),
            "hardware": hw,
        }

    def report_metrics(self):
        """上报指标到控制端"""
        if not self.server_url or not self.agent_id:
            return
        try:
            metrics = self.collect_metrics()
            data = json.dumps({"agent_id": self.agent_id, "metrics": metrics}).encode()
            req = Request(
                f"{self.server_url}/agents/{self.agent_id}/metrics",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urlopen(req, timeout=10)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 指标上报成功")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 上报失败: {e}")

    def _report_loop(self):
        """后台定时上报线程"""
        time.sleep(10)
        while True:
            self.report_metrics()
            time.sleep(self.report_interval)

    # ── HTTP 服务（通用）─────────────────────────────────────

    def _make_handler(self):
        agent = self

        class AgentHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

            def _send_json(self, data: dict, status: int = 200):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> dict:
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length)) if length else {}

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/ping":
                    self._send_json({"pong": True, "agent_id": agent.agent_id,
                                     "os": agent.get_os_info().get("os", "unknown")})
                elif path == "/metrics":
                    self._send_json(agent.collect_metrics())
                elif path == "/info":
                    self._send_json(agent.get_os_info())
                else:
                    self._send_json({"error": "not found"}, 404)

            def do_POST(self):
                path = urlparse(self.path).path
                if path == "/exec":
                    body = self._read_body()
                    command = body.get("command", "").strip()
                    timeout = int(body.get("timeout", 60))
                    if not command:
                        self._send_json({"error": "command is required"}, 400)
                        return
                    safe, reason = is_safe(command)
                    if not safe:
                        self._send_json({"success": False, "output": "", "error": reason})
                        return
                    self._send_json(agent.execute_command(command, timeout))
                else:
                    self._send_json({"error": "not found"}, 404)

        return AgentHandler

    def start(self):
        """启动 Agent：HTTP 服务 + 定时上报"""
        info = self.get_os_info()
        print(f"CyberAgentOps Agent 启动")
        print(f"  类型: {self.__class__.__name__}")
        print(f"  系统: {info.get('os')} {info.get('os_version', '')[:40]}")
        print(f"  主机: {info.get('hostname', socket.gethostname())}")
        print(f"  监听: {self.host}:{self.port}")
        if self.server_url:
            print(f"  上报: {self.server_url} 每{self.report_interval//60}分钟")
            threading.Thread(target=self._report_loop, daemon=True).start()

        server = HTTPServer((self.host, self.port), self._make_handler())
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Agent 已停止")
