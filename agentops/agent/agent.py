#!/usr/bin/env python3
"""
AgentOps 远端 Agent
运行在目标机器上，接收服务端下发的命令并执行，返回结果。
依赖：aiohttp（标准库 asyncio + http.server 作为降级方案）
"""
import asyncio
import json
import subprocess
import sys
import argparse
import platform
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ── 安全黑名单 ──────────────────────────────────────────────
DANGEROUS = [
    r"rm\s+-rf\s+/[^/]",   # rm -rf /xxx
    r"dd\s+if=/dev/zero",
    r"mkfs\.",
    r":\(\)\{.*\}",         # fork bomb
    r">\s*/dev/sd[a-z]",    # 直接写磁盘
]

def is_safe(command: str) -> tuple:
    for p in DANGEROUS:
        if re.search(p, command):
            return False, f"危险命令被拦截: {p}"
    return True, "ok"


def run_command(command: str, timeout: int = 60) -> dict:
    """同步执行命令，返回结果字典"""
    safe, reason = is_safe(command)
    if not safe:
        return {"success": False, "output": "", "error": reason}
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True,
            text=True, timeout=timeout
        )
        output = result.stdout or result.stderr
        return {"success": result.returncode == 0, "output": output, "error": ""}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": f"超时（{timeout}s）"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


def system_info() -> dict:
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "hostname": platform.node(),
    }


# ── HTTP 服务（纯标准库，无需额外依赖）────────────────────────
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
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/ping":
            self._send_json({"pong": True, "info": system_info()})
        elif path == "/info":
            self._send_json(system_info())
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
            result = run_command(command, timeout)
            self._send_json(result)
        else:
            self._send_json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="AgentOps Remote Agent")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    info = system_info()
    print(f"AgentOps Agent 启动")
    print(f"  系统: {info['os']} {info['os_version']}")
    print(f"  主机: {info['hostname']}")
    print(f"  监听: {args.host}:{args.port}")

    server = HTTPServer((args.host, args.port), AgentHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Agent 已停止")


if __name__ == "__main__":
    main()
