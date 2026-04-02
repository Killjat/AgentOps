#!/usr/bin/env python3
"""
AgentOps 远端 Agent
- 接收服务端命令并执行
- 每10分钟自动上报系统信息
"""
import argparse
import hashlib
import json
import os
import platform
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
from urllib.error import URLError

# ── 安全黑名单 ──────────────────────────────────────────────
DANGEROUS = [
    r"rm\s+-rf\s+/[^/]",
    r"dd\s+if=/dev/zero",
    r"mkfs\.",
    r":\(\)\{.*\}",
    r">\s*/dev/sd[a-z]",
]

def is_safe(command: str) -> tuple:
    for p in DANGEROUS:
        if re.search(p, command):
            return False, f"危险命令被拦截: {p}"
    return True, "ok"


def run_command(command: str, timeout: int = 60) -> dict:
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


# ── 系统信息采集 ─────────────────────────────────────────────

def _cmd(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL,
                                       timeout=5).decode().strip()
    except Exception:
        return ""


def get_cpu_usage() -> float:
    """读取 CPU 使用率（%）"""
    try:
        # Linux: /proc/stat
        with open("/proc/stat") as f:
            line = f.readline()
        vals = list(map(int, line.split()[1:]))
        idle = vals[3]
        total = sum(vals)
        time.sleep(0.2)
        with open("/proc/stat") as f:
            line = f.readline()
        vals2 = list(map(int, line.split()[1:]))
        idle2 = vals2[3]
        total2 = sum(vals2)
        return round(100 * (1 - (idle2 - idle) / (total2 - total)), 1)
    except Exception:
        return -1.0


def get_disk_usage() -> list:
    """磁盘使用情况"""
    result = []
    try:
        out = _cmd("df -h --output=target,size,used,avail,pcent 2>/dev/null || df -h")
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                result.append({
                    "mount": parts[0] if platform.system()=="Windows" else parts[-1] if len(parts)==5 else parts[5],
                    "size": parts[1], "used": parts[2],
                    "avail": parts[3], "use_pct": parts[4]
                })
    except Exception:
        pass
    return result[:5]  # 最多返回5个挂载点


def get_network_io() -> dict:
    """网络 IO 统计（读取两次算实时速率）"""
    def read_net():
        stats = {}
        try:
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    iface = parts[0].rstrip(":")
                    if iface == "lo":
                        continue
                    stats[iface] = {
                        "rx_bytes": int(parts[1]),
                        "tx_bytes": int(parts[9]),
                    }
        except Exception:
            pass
        return stats

    s1 = read_net()
    time.sleep(1)
    s2 = read_net()

    result = {}
    for iface in s1:
        if iface in s2:
            rx_speed = (s2[iface]["rx_bytes"] - s1[iface]["rx_bytes"])  # bytes/s
            tx_speed = (s2[iface]["tx_bytes"] - s1[iface]["tx_bytes"])
            result[iface] = {
                "rx_bytes_total": s2[iface]["rx_bytes"],
                "tx_bytes_total": s2[iface]["tx_bytes"],
                "rx_kbps": round(rx_speed / 1024, 1),
                "tx_kbps": round(tx_speed / 1024, 1),
            }
    return result
    """获取所有网卡 IP"""
    ips = {}
    try:
        # 公网 IP
        try:
            pub = urlopen(Request("https://api.ipify.org", headers={"User-Agent":"curl"}),
                          timeout=3).read().decode().strip()
            ips["public"] = pub
        except Exception:
            pass
        # 本地 IP
        out = _cmd("ip -4 addr show 2>/dev/null || ifconfig 2>/dev/null")
        for line in out.splitlines():
            m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
            if m and not m.group(1).startswith("127."):
                iface = "eth"
                ips[iface] = m.group(1)
                break
        # hostname
        ips["hostname"] = socket.gethostname()
    except Exception:
        pass
    return ips


def get_hardware_info() -> dict:
    """采集完整硬件信息"""
    info = {}

    # CPU 型号
    info["cpu_model"] = _cmd(
        "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2 | xargs"
    ) or _cmd("sysctl -n machdep.cpu.brand_string 2>/dev/null")

    # CPU 核心数
    info["cpu_cores"] = _cmd("nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null")

    # 主板序列号
    info["board_serial"] = (
        _cmd("cat /sys/class/dmi/id/board_serial 2>/dev/null") or
        _cmd("dmidecode -s baseboard-serial-number 2>/dev/null") or
        "N/A"
    )

    # 主板型号
    info["board_name"] = (
        _cmd("cat /sys/class/dmi/id/board_name 2>/dev/null") or
        _cmd("dmidecode -s baseboard-product-name 2>/dev/null") or
        "N/A"
    )

    # 磁盘 ID 列表
    disk_ids = _cmd("ls /dev/disk/by-id/ 2>/dev/null")
    info["disk_ids"] = [d for d in disk_ids.splitlines() if d and "part" not in d][:6]

    # 磁盘型号（备用）
    if not info["disk_ids"]:
        info["disk_ids"] = _cmd(
            "lsblk -d -o NAME,MODEL,SERIAL 2>/dev/null | tail -n +2"
        ).splitlines()[:4]

    # 网卡 MAC 地址
    macs = {}
    mac_out = _cmd("ip link show 2>/dev/null || ifconfig -a 2>/dev/null")
    iface = None
    for line in mac_out.splitlines():
        # ip link 格式
        m = re.match(r'\d+:\s+(\S+):', line)
        if m:
            iface = m.group(1).rstrip(":")
        m2 = re.search(r'(?:ether|link/ether)\s+([0-9a-f:]{17})', line, re.I)
        if m2 and iface and iface != "lo":
            macs[iface] = m2.group(1)
    info["mac_addresses"] = macs

    # 内存总量
    mem = _cmd("grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}'")
    if mem:
        info["memory_mb"] = round(int(mem) / 1024)

    # 硬件指纹（基于以上信息生成）
    raw = "|".join([
        info.get("cpu_model", ""),
        info.get("board_serial", ""),
        str(info.get("disk_ids", "")),
        str(info.get("mac_addresses", "")),
        socket.gethostname(),
    ])
    info["hw_fingerprint"] = hashlib.sha256(raw.encode()).hexdigest()[:16]

    return info


def collect_metrics() -> dict:
    """采集完整系统指标"""
    hw = get_hardware_info()
    return {
        "timestamp": datetime.now().isoformat(),
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_version": platform.version()[:80],
        "cpu_usage": get_cpu_usage(),
        "disk": get_disk_usage(),
        "network": get_network_ips(),
        "network_io": get_network_io(),
        "hardware": hw,
    }


# ── 上报线程 ─────────────────────────────────────────────────

SERVER_URL = ""   # 启动时设置
AGENT_ID = ""
REPORT_INTERVAL = 600  # 10分钟


def report_loop():
    """后台线程：每10分钟上报一次"""
    time.sleep(10)  # 启动后稍等再上报
    while True:
        try:
            if SERVER_URL and AGENT_ID:
                metrics = collect_metrics()
                data = json.dumps({"agent_id": AGENT_ID, "metrics": metrics}).encode()
                req = Request(
                    f"{SERVER_URL}/agents/{AGENT_ID}/metrics",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                urlopen(req, timeout=10)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 指标上报成功")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 上报失败: {e}")
        time.sleep(REPORT_INTERVAL)


# ── HTTP 服务 ─────────────────────────────────────────────────

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
            self._send_json({"pong": True, "agent_id": AGENT_ID,
                             "hostname": socket.gethostname()})
        elif path == "/metrics":
            self._send_json(collect_metrics())
        elif path == "/info":
            self._send_json({"os": platform.system(), "os_version": platform.version(),
                             "hostname": socket.gethostname(), "python": sys.version.split()[0]})
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
            self._send_json(run_command(command, timeout))
        else:
            self._send_json({"error": "not found"}, 404)


def main():
    global SERVER_URL, AGENT_ID

    parser = argparse.ArgumentParser(description="AgentOps Remote Agent")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--server", default="", help="控制端地址，如 http://1.2.3.4:8000")
    parser.add_argument("--agent-id", default="", help="Agent ID")
    args = parser.parse_args()

    SERVER_URL = args.server.rstrip("/")
    AGENT_ID = args.agent_id

    print(f"CyberAgentOps Agent 启动")
    print(f"  系统: {platform.system()} {platform.version()[:40]}")
    print(f"  主机: {socket.gethostname()}")
    print(f"  监听: {args.host}:{args.port}")
    if SERVER_URL:
        print(f"  上报: {SERVER_URL} 每{REPORT_INTERVAL//60}分钟")
        threading.Thread(target=report_loop, daemon=True).start()

    server = HTTPServer((args.host, args.port), AgentHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Agent 已停止")


if __name__ == "__main__":
    main()
