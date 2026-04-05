"""WindowsAgent - Windows 系统 Agent"""
import hashlib
import platform
import re
import socket
import subprocess
import time
from typing import Dict, List, Any
from urllib.request import urlopen

from base import BaseAgent


class WindowsAgent(BaseAgent):
    """Windows Agent，通过 HTTP 服务接受控制端命令"""

    def get_os_info(self) -> Dict[str, Any]:
        return {
            "os": "Windows",
            "os_version": platform.version(),
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "arch": platform.machine(),
        }

    def get_cpu_usage(self) -> float:
        try:
            out = self._cmd(
                'powershell -Command "Get-WmiObject Win32_Processor | '
                'Measure-Object -Property LoadPercentage -Average | '
                'Select-Object -ExpandProperty Average"'
            )
            return float(out.strip())
        except Exception:
            return -1.0

    def get_disk_usage(self) -> List[Dict[str, Any]]:
        result = []
        try:
            out = self._cmd(
                'powershell -Command "Get-PSDrive -PSProvider FileSystem | '
                'Select-Object Name,Used,Free | ConvertTo-Csv -NoTypeInformation"'
            )
            for line in out.splitlines()[1:]:
                parts = line.strip('"').split('","')
                if len(parts) >= 3 and parts[1].isdigit():
                    used = int(parts[1])
                    free = int(parts[2]) if parts[2].isdigit() else 0
                    total = used + free
                    if total > 0:
                        result.append({
                            "mount": parts[0] + ":\\",
                            "size": f"{total // (1024**3)}G",
                            "used": f"{used // (1024**3)}G",
                            "avail": f"{free // (1024**3)}G",
                            "use_pct": f"{round(used/total*100)}%"
                        })
        except Exception:
            pass
        return result[:5]

    def get_network_ips(self) -> Dict[str, Any]:
        ips = {"hostname": socket.gethostname()}
        try:
            out = self._cmd("ipconfig")
            for line in out.splitlines():
                m = re.search(r'IPv4.*?:\s+(\d+\.\d+\.\d+\.\d+)', line)
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
        # Windows 网络 IO 通过 PowerShell 获取
        try:
            out1 = self._cmd(
                'powershell -Command "Get-NetAdapterStatistics | '
                'Select-Object Name,ReceivedBytes,SentBytes | ConvertTo-Csv -NoTypeInformation"'
            )
            time.sleep(1)
            out2 = self._cmd(
                'powershell -Command "Get-NetAdapterStatistics | '
                'Select-Object Name,ReceivedBytes,SentBytes | ConvertTo-Csv -NoTypeInformation"'
            )
            result = {}
            lines1 = {l.split(',')[0].strip('"'): l for l in out1.splitlines()[1:] if l}
            for line in out2.splitlines()[1:]:
                parts = [p.strip('"') for p in line.split(',')]
                if len(parts) >= 3 and parts[0] in lines1:
                    prev = [p.strip('"') for p in lines1[parts[0]].split(',')]
                    try:
                        rx_diff = (int(parts[1]) - int(prev[1])) / 1024
                        tx_diff = (int(parts[2]) - int(prev[2])) / 1024
                        result[parts[0]] = {
                            "rx_kbps": round(rx_diff, 1),
                            "tx_kbps": round(tx_diff, 1),
                        }
                    except Exception:
                        pass
            return result
        except Exception:
            return {}

    def get_hardware_info(self) -> Dict[str, Any]:
        hw = {}
        hw["cpu_model"] = self._cmd(
            'powershell -Command "(Get-WmiObject Win32_Processor).Name"'
        )
        hw["cpu_cores"] = self._cmd(
            'powershell -Command "(Get-WmiObject Win32_Processor).NumberOfCores"'
        )
        hw["board_serial"] = self._cmd(
            'powershell -Command "(Get-WmiObject Win32_BaseBoard).SerialNumber"'
        ) or "N/A"
        hw["board_name"] = self._cmd(
            'powershell -Command "(Get-WmiObject Win32_BaseBoard).Product"'
        ) or "N/A"

        mem_bytes = self._cmd(
            'powershell -Command "(Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory"'
        )
        hw["memory_mb"] = round(int(mem_bytes) / (1024**2)) if mem_bytes.isdigit() else 0

        # 磁盘序列号
        disk_serials = self._cmd(
            'powershell -Command "Get-WmiObject Win32_DiskDrive | Select-Object SerialNumber | ConvertTo-Csv -NoTypeInformation"'
        )
        hw["disk_ids"] = [l.strip('"') for l in disk_serials.splitlines()[1:] if l.strip('"')][:4]

        # MAC 地址
        macs = {}
        mac_out = self._cmd(
            'powershell -Command "Get-NetAdapter | Select-Object Name,MacAddress | ConvertTo-Csv -NoTypeInformation"'
        )
        for line in mac_out.splitlines()[1:]:
            parts = [p.strip('"') for p in line.split(',')]
            if len(parts) >= 2 and parts[1]:
                macs[parts[0]] = parts[1]
        hw["mac_addresses"] = macs

        raw = "|".join([hw.get("cpu_model", ""), hw.get("board_serial", ""),
                        str(hw.get("disk_ids", "")), str(macs), socket.gethostname()])
        hw["hw_fingerprint"] = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return hw

    def execute_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """Windows 命令执行，支持 cmd 和 PowerShell"""
        try:
            if any(kw in command for kw in ["Get-", "Set-", "New-", "Remove-", "$"]):
                cmd = ["powershell", "-Command", command]
            else:
                cmd = ["cmd", "/c", command]

            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, encoding="utf-8", errors="replace"
            )
            output = result.stdout or result.stderr
            return {"success": result.returncode == 0, "output": output, "error": ""}
        except subprocess.TimeoutExpired:
            return {"success": False, "output": "", "error": f"超时（{timeout}s）"}
        except Exception as e:
            return {"success": False, "output": "", "error": str(e)}

    def _cmd(self, cmd: str) -> str:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True,
                text=True, timeout=30, encoding="utf-8", errors="replace"
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            print(f"[Agent] 命令超时: {cmd[:100]}")
            return ""
        except Exception as e:
            print(f"[Agent] 命令执行失败: {cmd[:100]}, error: {e}")
            return ""

    def discover_apps(self) -> Dict[str, Any]:
        """发现 Windows 本地已部署的应用（服务、监听端口）"""
        result: Dict[str, Any] = {
            "services": [],
            "containers": [],
            "ports": [],
            "agent_id": self.agent_id,
            "hostname": self.get_os_info().get("hostname", "")
        }

        # 1. 扫描 Windows 服务
        try:
            services_out = self._cmd(
                'powershell -Command "Get-Service | Where-Object {$_.Status -eq \'Running\'} | '
                'Select-Object Name,DisplayName | ConvertTo-Csv -NoTypeInformation"'
            )
            for line in services_out.splitlines()[1:]:
                parts = [p.strip('"') for p in line.split(',')]
                if len(parts) >= 2 and parts[0]:
                    # 排除系统服务
                    svc_name = parts[0].lower()
                    if any(x in svc_name for x in ['windows', 'microsoft', 'system', 'agent']):
                        continue

                    result["services"].append({
                        "name": parts[0],
                        "description": parts[1][:100],
                        "port": "",
                        "status": "running"
                    })
        except Exception:
            pass

        # 2. 扫描监听端口
        try:
            ports_out = self._cmd('netstat -ano | findstr LISTENING')
            for line in ports_out.splitlines():
                # 解析端口和进程
                m = re.search(r':(\d+)\s+.*?(\d+)$', line.strip())
                if m:
                    port = m.group(1)
                    pid = m.group(2)
                    # 获取进程名
                    proc_name = self._cmd(f'tasklist /FI "PID eq {pid}" /NH /FO CSV').strip('"')
                    result["ports"].append({
                        "port": port,
                        "process": proc_name or "unknown",
                        "pid": pid
                    })
        except Exception:
            pass

        result["tools"] = self.discover_tools()
        return result
