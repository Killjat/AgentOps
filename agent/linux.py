"""LinuxAgent - Linux/macOS 系统 Agent"""
import hashlib
import platform
import re
import socket
import subprocess
import time
from typing import Dict, List, Any
from urllib.request import urlopen

from base import BaseAgent


class LinuxAgent(BaseAgent):
    """Linux / macOS Agent，通过 HTTP 服务接受控制端命令"""

    def get_os_info(self) -> Dict[str, Any]:
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

    def get_disk_usage(self) -> List[Dict[str, Any]]:
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

    def get_network_ips(self) -> Dict[str, Any]:
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

    def get_network_io(self) -> Dict[str, Any]:
        def read_net() -> Dict[str, Any]:
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

    def get_hardware_info(self) -> Dict[str, Any]:
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
        iface = ""
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
                cmd, shell=True, stderr=subprocess.DEVNULL, timeout=30
            ).decode().strip()
        except subprocess.TimeoutExpired:
            print(f"[Agent] 命令超时: {cmd[:100]}")
            return ""
        except Exception as e:
            print(f"[Agent] 命令执行失败: {cmd[:100]}, error: {e}")
            return ""

    def discover_apps(self) -> Dict[str, Any]:
        """发现本地已部署的应用（systemd服务、Docker容器、监听端口）"""
        print(f"[Agent] discover_apps 开始执行")
        result: Dict[str, Any] = {
            "services": [],
            "containers": [],
            "ports": [],
            "agent_id": self.agent_id,
            "hostname": self.get_os_info().get("hostname", "")
        }

        # 1. 扫描 systemd 服务
        print(f"[Agent] 扫描 systemd 服务...")
        try:
            services_out = self._cmd("systemctl list-units --type=service --state=running --no-pager 2>/dev/null")
            for line in services_out.splitlines()[1:]:  # 跳过表头
                if not line.strip() or any(x in line for x in ['UNIT', 'LOAD', 'ssh', 'agent', 'docker']):
                    continue

                parts = line.split()
                if len(parts) >= 4:
                    svc_name = parts[0]
                    if not svc_name.endswith('.service'):
                        continue

                    # 获取服务描述
                    desc = self._cmd(f"systemctl show {svc_name} -p Description --value 2>/dev/null") or svc_name
                    exec_start = self._cmd(f"systemctl show {svc_name} -p ExecStart --value 2>/dev/null")

                    # 尝试查找端口
                    port = ""
                    if exec_start:
                        # 通过进程名查找端口
                        proc_name = svc_name.split('.')[0]
                        port_match = re.search(r':(\d+)', self._cmd(f"ss -tlnp | grep '{proc_name}' 2>/dev/null | head -1"))
                        if port_match:
                            port = port_match.group(1)

                    if port or any(k in exec_start.lower() for k in ['python', 'node', 'java', 'gunicorn', 'uvicorn']):
                        result["services"].append({
                            "name": svc_name,
                            "description": desc[:100],
                            "port": port,
                            "exec": exec_start[:200],
                            "status": "running"
                        })
        except Exception as e:
            print(f"[Agent] systemd 扫描错误: {e}")

        # 2. 扫描 Docker 容器
        print(f"[Agent] 扫描 Docker 容器...")
        try:
            docker_out = self._cmd("docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>/dev/null")
            for line in docker_out.splitlines():
                if '|' not in line:
                    continue
                name, status, ports = line.split('|', 2)
                if name and status:
                    # 提取端口号
                    port_match = re.search(r':(\d+)->', ports)
                    port = port_match.group(1) if port_match else ""

                    result["containers"].append({
                        "name": name,
                        "status": status,
                        "ports": ports,
                        "port": port
                    })
        except Exception as e:
            print(f"[Agent] Docker 扫描错误: {e}")

        # 3. 扫描常见端口（并发）
        print(f"[Agent] 扫描常见端口...")
        common_ports = [80, 443, 8000, 8080, 3000, 5000, 9000, 5001, 5432, 3306, 6379, 27017]

        def check_port(port):
            try:
                port_info = self._cmd(f"ss -tlnp 2>/dev/null | grep ':{port} ' | head -1")
                if port_info:
                    proc_match = re.search(r'pid=(\d+),name=([^,\s]+)', port_info)
                    return {
                        "port": port,
                        "process": proc_match.group(2) if proc_match else "unknown",
                        "info": port_info[:100]
                    }
            except Exception:
                pass
            return None

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            port_results = list(executor.map(check_port, common_ports))
        result["ports"] = [r for r in port_results if r is not None]

        print(f"[Agent] discover_apps 完成，共发现 {len(result['services'])} 个服务, {len(result['containers'])} 个容器, {len(result['ports'])} 个端口")
        return result
