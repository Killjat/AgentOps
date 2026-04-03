"""LinuxAgent - Linux/macOS 系统 Agent"""
import hashlib
import platform
import re
import socket
import subprocess
import time
from urllib.request import urlopen

from .base import BaseAgent


class LinuxAgent(BaseAgent):
    """Linux / macOS Agent，通过 HTTP 服务接受控制端命令"""

    def get_os_info(self) -> dict:
        return {
            "os": platform.system(),
            "os_version": platform.version()[:80],
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "arch": platform.machine(),
        }

    def get_cpu_usage(self) -> float:
        try:
            with open("/proc/stat") as f:
                v1 = list(map(int, f.readline().split()[1:]))
            time.sleep(0.5)
            with open("/proc/stat") as f:
                v2 = list(map(int, f.readline().split()[1:]))
            return round(100 * (1 - (v2[3] - v1[3]) / (sum(v2) - sum(v1))), 1)
        except Exception:
            return -1.0

    def get_disk_usage(self) -> list:
        result = []
        try:
            out = self._cmd("df -h")
            for line in out.splitlines()[1:]:
                p = line.split()
                if len(p) >= 6:
                    result.append({"mount": p[5], "size": p[1],
                                   "used": p[2], "avail": p[3], "use_pct": p[4]})
        except Exception:
            pass
        return result[:5]

    def get_network_ips(self) -> dict:
        ips = {"hostname": socket.gethostname()}
        try:
            out = self._cmd("ip -4 addr show 2>/dev/null || ifconfig 2>/dev/null")
            for line in out.splitlines():
                m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
                if m and not m.group(1).startswith("127."):
                    ips["eth"] = m.group(1)
                    break
            try:
                pub = urlopen("https://api.ipify.org", timeout=3).read().decode()
                ips["public"] = pub
            except Exception:
                pass
        except Exception:
            pass
        return ips

    def get_network_io(self) -> dict:
        def read_net():
            s = {}
            try:
                with open("/proc/net/dev") as f:
                    for line in f.readlines()[2:]:
                        p = line.split()
                        if len(p) >= 10:
                            ifc = p[0].rstrip(":")
                            if ifc != "lo":
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
                    "rx_bytes_total": s2[ifc]["rx"],
                    "tx_bytes_total": s2[ifc]["tx"],
                }
        return result

    def get_hardware_info(self) -> dict:
        hw = {}
        hw["cpu_model"] = self._cmd(
            "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2 | xargs"
        ) or self._cmd("sysctl -n machdep.cpu.brand_string 2>/dev/null")
        hw["cpu_cores"] = self._cmd("nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null")
        hw["board_serial"] = self._cmd("cat /sys/class/dmi/id/board_serial 2>/dev/null") or "N/A"
        hw["board_name"] = self._cmd("cat /sys/class/dmi/id/board_name 2>/dev/null") or "N/A"

        mem_kb = self._cmd("grep MemTotal /proc/meminfo | awk '{print $2}'")
        hw["memory_mb"] = round(int(mem_kb) / 1024) if mem_kb.isdigit() else 0

        disk_ids_raw = self._cmd("ls /dev/disk/by-id/ 2>/dev/null")
        hw["disk_ids"] = [d for d in disk_ids_raw.splitlines() if d and "part" not in d][:6]
        if not hw["disk_ids"]:
            hw["disk_ids"] = self._cmd(
                "lsblk -d -o NAME,MODEL,SERIAL 2>/dev/null | tail -n +2"
            ).splitlines()[:4]

        macs = {}
        for line in self._cmd("ip link show 2>/dev/null").splitlines():
            m = re.match(r'\d+:\s+(\S+):', line)
            if m:
                iface = m.group(1).rstrip(":")
            m2 = re.search(r'link/ether\s+([0-9a-f:]{17})', line, re.I)
            if m2 and iface and iface != "lo":
                macs[iface] = m2.group(1)
        hw["mac_addresses"] = macs

        raw = "|".join([hw.get("cpu_model", ""), hw.get("board_serial", ""),
                        str(hw.get("disk_ids", "")), str(macs), socket.gethostname()])
        hw["hw_fingerprint"] = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return hw

    def execute_command(self, command: str, timeout: int = 60) -> dict:
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

    def _cmd(self, cmd: str) -> str:
        try:
            return subprocess.check_output(
                cmd, shell=True, stderr=subprocess.DEVNULL, timeout=5
            ).decode().strip()
        except Exception:
            return ""
