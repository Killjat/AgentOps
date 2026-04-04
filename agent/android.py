"""AndroidAgent - Android/Termux 系统 Agent"""
import hashlib
import platform
import re
import socket
import subprocess
import time
from typing import Dict, List, Any
from urllib.request import urlopen

from base import BaseAgent


class AndroidAgent(BaseAgent):
    """Android Agent（运行在 Termux 环境）"""

    def get_os_info(self) -> Dict[str, Any]:
        return {
            "os": "Android",
            "os_version": self._cmd("getprop ro.build.version.release") or platform.version()[:40],
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "arch": platform.machine(),
            "model": self._cmd("getprop ro.product.model") or "Unknown",
            "brand": self._cmd("getprop ro.product.brand") or "Unknown",
        }

    def get_cpu_usage(self) -> float:
        try:
            with open("/proc/stat") as f:
                v1 = list(map(int, f.readline().split()[1:]))
            time.sleep(0.5)
            with open("/proc/stat") as f:
                v2 = list(map(int, f.readline().split()[1:]))
            idle_delta = v2[3] - v1[3]
            total_delta = sum(v2) - sum(v1)
            return round(100 * (1 - idle_delta / total_delta), 1) if total_delta else -1.0
        except Exception:
            return -1.0

    def get_disk_usage(self) -> List[Dict[str, Any]]:
        result = []
        try:
            out = self._cmd("df -h /data /sdcard 2>/dev/null || df -h")
            for line in out.splitlines()[1:]:
                p = line.split()
                if len(p) >= 6:
                    result.append({"mount": p[5], "size": p[1],
                                   "used": p[2], "avail": p[3], "use_pct": p[4]})
        except Exception:
            pass
        return result[:5]

    def get_network_ips(self) -> Dict[str, Any]:
        ips = {"hostname": socket.gethostname()}
        try:
            # Android 用 ip addr 或 ifconfig
            out = self._cmd("ip -4 addr show 2>/dev/null || ifconfig 2>/dev/null")
            for line in out.splitlines():
                m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
                if m and not m.group(1).startswith("127."):
                    ips["eth"] = m.group(1)
                    break
            try:
                pub = urlopen("https://api.ipify.org", timeout=5).read().decode()
                ips["public"] = pub
            except Exception:
                pass
        except Exception:
            pass
        return ips

    def get_network_io(self) -> Dict[str, Any]:
        def read_net():
            s = {}
            try:
                with open("/proc/net/dev") as f:
                    for line in f.readlines()[2:]:
                        p = line.split()
                        if len(p) >= 10:
                            ifc = p[0].rstrip(":")
                            if ifc not in ("lo", "dummy0"):
                                s[ifc] = {"rx": int(p[1]), "tx": int(p[9])}
            except Exception:
                pass
            return s
        s1 = read_net()
        time.sleep(1)
        s2 = read_net()
        result = {}
        for ifc in s1:
            if ifc in s2:
                result[ifc] = {
                    "rx_kbps": round((s2[ifc]["rx"] - s1[ifc]["rx"]) / 1024, 1),
                    "tx_kbps": round((s2[ifc]["tx"] - s1[ifc]["tx"]) / 1024, 1),
                }
        return result

    def get_hardware_info(self) -> Dict[str, Any]:
        hw = {}
        hw["cpu_model"] = self._cmd("getprop ro.hardware") or self._cmd("cat /proc/cpuinfo | grep Hardware | head -1 | cut -d: -f2 | xargs")
        hw["cpu_cores"] = self._cmd("nproc 2>/dev/null") or str(platform.processor())
        hw["board_name"] = self._cmd("getprop ro.product.model")
        hw["board_serial"] = self._cmd("getprop ro.serialno") or "N/A"

        mem_kb = self._cmd("grep MemTotal /proc/meminfo | awk '{print $2}'")
        hw["memory_mb"] = round(int(mem_kb) / 1024) if mem_kb.isdigit() else 0

        hw["disk_ids"] = []
        hw["mac_addresses"] = {}

        # Android ID 作为唯一标识
        android_id = self._cmd("settings get secure android_id 2>/dev/null") or ""
        raw = "|".join([hw.get("cpu_model", ""), hw.get("board_serial", ""),
                        android_id, socket.gethostname()])
        hw["hw_fingerprint"] = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return hw

    def execute_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=timeout
            )
            output = result.stdout or result.stderr
            return {"success": result.returncode == 0, "output": output, "error": ""}
        except subprocess.TimeoutExpired:
            return {"success": False, "output": "", "error": f"超时（{timeout}s）"}
        except Exception as e:
            return {"success": False, "output": "", "error": str(e)}

    def _cmd(self, cmd: str) -> str:
        try:
            return subprocess.check_output(
                cmd, shell=True, stderr=subprocess.DEVNULL, timeout=10
            ).decode().strip()
        except Exception:
            return ""

    def discover_apps(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "services": [],
            "containers": [],
            "ports": [],
            "agent_id": self.agent_id,
            "hostname": socket.gethostname()
        }
        # 扫描监听端口
        common_ports = [80, 443, 8000, 8080, 3000, 5000, 9000]
        for port in common_ports:
            try:
                out = self._cmd(f"ss -tlnp 2>/dev/null | grep ':{port} ' | head -1")
                if out:
                    result["ports"].append({
                        "port": port,
                        "process": "unknown",
                        "info": out[:100]
                    })
            except Exception:
                pass
        return result
