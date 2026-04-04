"""MacAgent - macOS 系统 Agent"""
import hashlib
import platform
import re
import socket
import subprocess
import time
from typing import Dict, List, Any
from urllib.request import urlopen

from base import BaseAgent


class MacAgent(BaseAgent):
    """macOS Agent"""

    def get_os_info(self) -> Dict[str, Any]:
        return {
            "os": "macOS",
            "os_version": self._cmd("sw_vers -productVersion") or platform.mac_ver()[0],
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "arch": platform.machine(),
        }

    def get_cpu_usage(self) -> float:
        try:
            out = self._cmd("top -l 1 -n 0 | grep 'CPU usage'")
            m = re.search(r'(\d+\.\d+)%\s+user', out)
            if m:
                return float(m.group(1))
        except Exception:
            pass
        return -1.0

    def get_disk_usage(self) -> List[Dict[str, Any]]:
        result = []
        try:
            out = self._cmd("df -h")
            for line in out.splitlines()[1:]:
                p = line.split()
                if len(p) >= 9 and p[0].startswith('/dev/'):
                    result.append({
                        "mount": p[8],
                        "size": p[1],
                        "used": p[2],
                        "avail": p[3],
                        "use_pct": p[4]
                    })
        except Exception:
            pass
        return result[:5]

    def get_network_ips(self) -> Dict[str, Any]:
        ips = {"hostname": socket.gethostname()}
        try:
            out = self._cmd("ifconfig")
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

    def get_network_io(self) -> Dict[str, Any]:
        try:
            def read_net():
                out = self._cmd("netstat -ib")
                s = {}
                for line in out.splitlines()[1:]:
                    p = line.split()
                    if len(p) >= 10 and p[0] not in ('lo0', 'Name'):
                        try:
                            s[p[0]] = {"rx": int(p[6]), "tx": int(p[9])}
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
        except Exception:
            return {}

    def get_hardware_info(self) -> Dict[str, Any]:
        hw = {}
        hw["cpu_model"] = self._cmd("sysctl -n machdep.cpu.brand_string 2>/dev/null")
        hw["cpu_cores"] = self._cmd("sysctl -n hw.ncpu")
        mem_bytes = self._cmd("sysctl -n hw.memsize")
        hw["memory_mb"] = round(int(mem_bytes) / (1024 * 1024)) if mem_bytes.isdigit() else 0
        hw["board_name"] = self._cmd("system_profiler SPHardwareDataType 2>/dev/null | grep 'Model Name' | cut -d: -f2 | xargs")
        hw["board_serial"] = self._cmd("system_profiler SPHardwareDataType 2>/dev/null | grep 'Serial Number' | cut -d: -f2 | xargs")

        # 磁盘序列号
        disk_out = self._cmd("system_profiler SPStorageDataType 2>/dev/null | grep 'Volume UUID' | head -3")
        hw["disk_ids"] = [l.strip().split(": ")[-1] for l in disk_out.splitlines() if l.strip()][:3]

        # MAC 地址
        macs = {}
        out = self._cmd("ifconfig")
        iface = ""
        for line in out.splitlines():
            m = re.match(r'^(\w+\d*):', line)
            if m:
                iface = m.group(1)
            m2 = re.search(r'ether\s+([0-9a-f:]{17})', line, re.I)
            if m2 and iface and iface != "lo0":
                macs[iface] = m2.group(1)
        hw["mac_addresses"] = macs

        raw = "|".join([hw.get("cpu_model", ""), hw.get("board_serial", ""),
                        str(hw.get("disk_ids", "")), str(macs), socket.gethostname()])
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
                cmd, shell=True, stderr=subprocess.DEVNULL, timeout=15
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

        # 1. launchctl 服务
        try:
            out = self._cmd("launchctl list 2>/dev/null | grep -v '^-' | head -30")
            for line in out.splitlines()[1:]:
                p = line.split('\t')
                if len(p) >= 3 and p[2] and not p[2].startswith('com.apple'):
                    result["services"].append({
                        "name": p[2],
                        "description": p[2],
                        "port": "",
                        "status": "running" if p[0] != "-" else "stopped"
                    })
        except Exception:
            pass

        # 2. Docker 容器
        try:
            docker_out = self._cmd("docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>/dev/null")
            for line in docker_out.splitlines():
                if '|' not in line:
                    continue
                name, status, ports = line.split('|', 2)
                port_match = re.search(r':(\d+)->', ports)
                result["containers"].append({
                    "name": name,
                    "status": status,
                    "ports": ports,
                    "port": port_match.group(1) if port_match else ""
                })
        except Exception:
            pass

        # 3. 监听端口（用 lsof）
        common_ports = [80, 443, 8000, 8080, 3000, 5000, 9000, 5001, 5432, 3306, 6379, 27017]
        import concurrent.futures

        def check_port(port):
            try:
                out = self._cmd(f"lsof -i :{port} -sTCP:LISTEN 2>/dev/null | tail -1")
                if out:
                    m = re.match(r'(\S+)\s+(\d+)', out)
                    return {
                        "port": port,
                        "process": m.group(1) if m else "unknown",
                        "info": out[:100]
                    }
            except Exception:
                pass
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            port_results = list(executor.map(check_port, common_ports))
        result["ports"] = [r for r in port_results if r is not None]

        return result
