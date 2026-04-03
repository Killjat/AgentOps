"""SSH 自动部署 - 检测目标系统 OS，上传对应 Agent，启动服务"""
import asyncio
import uuid
import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

import asyncssh

from models import AgentInfo, OSType, AgentStatus, ConnectionType, DeviceType, RemoteHost

# Agent 目录路径
AGENT_DIR = Path(__file__).parent.parent / "agent"
AGENT_PORT = 9000

# 需要上传的 Agent 文件
AGENT_FILES = ["agent.py", "base.py", "linux.py", "windows.py", "__main__.py", "__init__.py"]


async def _connect(host: RemoteHost) -> asyncssh.SSHClientConnection:
    kwargs = dict(
        host=host.host,
        port=host.port,
        username=host.username,
        known_hosts=None,
    )
    if host.password:
        kwargs["password"] = host.password
        kwargs["preferred_auth"] = "password,keyboard-interactive"
    if host.ssh_key:
        kwargs["client_keys"] = [host.ssh_key]
    return await asyncssh.connect(**kwargs)


async def _detect_os(conn: asyncssh.SSHClientConnection) -> Tuple[OSType, str]:
    """检测目标系统 OS"""
    # 先尝试 uname（Linux/macOS），指定 UTF-8 编码
    try:
        result = await conn.run("uname -s && uname -r", check=False,
                                encoding="utf-8", errors="replace")
        if result.exit_status == 0:
            lines = result.stdout.strip().splitlines()
            kernel = lines[0].lower() if lines else ""
            version = lines[1] if len(lines) > 1 else ""
            if "linux" in kernel:
                distro = await conn.run(
                    "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
                    check=False, encoding="utf-8", errors="replace"
                )
                return OSType.LINUX, distro.stdout.strip() or f"Linux {version}"
            elif "darwin" in kernel:
                return OSType.MACOS, f"macOS {version}"
    except Exception:
        pass

    # 尝试 Windows（GBK 编码）
    try:
        result = await conn.run("ver", check=False,
                                encoding="gbk", errors="replace")
        if result.exit_status == 0 and result.stdout:
            ver_str = result.stdout.strip()
            if "windows" in ver_str.lower() or "microsoft" in ver_str.lower():
                return OSType.WINDOWS, ver_str.replace("\r\n", "").replace("\n", "")
    except Exception:
        pass

    # 再试一次用 latin-1（兼容所有字节）
    try:
        result = await conn.run("ver", check=False,
                                encoding="latin-1", errors="replace")
        if result.exit_status == 0 and result.stdout:
            return OSType.WINDOWS, result.stdout.strip()[:80]
    except Exception:
        pass

    return OSType.UNKNOWN, "Unknown"


async def _check_python(conn: asyncssh.SSHClientConnection, os_type: OSType) -> str:
    candidates = ["python", "python3"] if os_type == OSType.WINDOWS else ["python3", "python"]
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    for py in candidates:
        r = await conn.run(f"{py} --version", check=False,
                           encoding=enc, errors="replace")
        if r.exit_status == 0:
            return py
    raise RuntimeError("目标系统未安装 Python，请先安装 Python 3.8+")


async def _upload_agent(conn: asyncssh.SSHClientConnection, deploy_dir: str, os_type: OSType):
    """上传所有 Agent 文件"""
    async with conn.start_sftp_client() as sftp:
        for fname in AGENT_FILES:
            local = AGENT_DIR / fname
            if not local.exists():
                continue
            if os_type == OSType.WINDOWS:
                # Windows 路径：确保只有一个反斜杠
                remote = f"{deploy_dir}\\{fname}".replace("\\\\", "\\")
            else:
                remote = f"{deploy_dir}/{fname}"
            await sftp.put(str(local), remote)
            print(f"[deploy] 上传 {fname}")


async def _install_deps(conn: asyncssh.SSHClientConnection, py: str, os_type: OSType):
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    if os_type == OSType.WINDOWS:
        await conn.run(f"{py} -m pip install aiohttp --quiet",
                       check=False, encoding=enc, errors="replace")
    else:
        await conn.run(
            f"dnf install -y python3-pip 2>/dev/null || apt-get install -y python3-pip 2>/dev/null || true",
            check=False, encoding="utf-8", errors="replace"
        )
        await conn.run(
            f"{py} -m pip install aiohttp --quiet --break-system-packages 2>/dev/null "
            f"|| {py} -m pip install aiohttp --quiet "
            f"|| pip3 install aiohttp --quiet",
            check=False, encoding="utf-8", errors="replace"
        )


async def deploy(host: RemoteHost) -> AgentInfo:
    """完整部署流程"""
    print(f"[deploy] 连接 {host.username}@{host.host}:{host.port} ...")
    conn = await _connect(host)

    try:
        # 1. 检测 OS
        os_type, os_version = await _detect_os(conn)
        print(f"[deploy] 检测到系统: {os_version} ({os_type})")

        # 2. 检测 Python
        py = await _check_python(conn, os_type)
        print(f"[deploy] Python: {py}")

        # 3. 创建部署目录
        if os_type == OSType.WINDOWS:
            deploy_dir = host.deploy_dir.replace("/", "\\") if host.deploy_dir else "C:\\agentops"
            # 避免双重转义
            deploy_dir = deploy_dir.replace("\\\\", "\\")
            await conn.run(f'cmd /c "if not exist {deploy_dir} mkdir {deploy_dir}"',
                           check=False, encoding="latin-1", errors="replace")
        else:
            deploy_dir = host.deploy_dir or "/opt/agentops"
            await conn.run(f"mkdir -p {deploy_dir}", check=False,
                           encoding="utf-8", errors="replace")

        # 4. 上传 Agent 文件
        print(f"[deploy] 上传 Agent 文件 -> {deploy_dir} ...")
        await _upload_agent(conn, deploy_dir, os_type)

        # 5. 安装依赖
        print("[deploy] 安装依赖 ...")
        await _install_deps(conn, py, os_type)

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        server_url = os.getenv("SERVER_URL", "")
        extra = f"--agent-id {agent_id} --type {'windows' if os_type == OSType.WINDOWS else 'linux'}"
        if server_url:
            extra += f" --server {server_url}"

        # 6. 启动 Agent
        print(f"[deploy] 启动 Agent (port {AGENT_PORT}) ...")
        if os_type == OSType.WINDOWS:
            # Windows: 用 pythonw 后台运行，不弹窗
            start_cmd = (
                f'cmd /c "cd /d {deploy_dir} && '
                f'taskkill /f /im pythonw.exe 2>nul & '
                f'start /b pythonw agent.py --port {AGENT_PORT} {extra} '
                f'> agent.log 2>&1"'
            )
            await conn.run(start_cmd, check=False)
        else:
            start_cmd = (
                f"cd {deploy_dir} && "
                f"pkill -f 'agent.py' 2>/dev/null; "
                f"nohup {py} agent.py --port {AGENT_PORT} {extra} "
                f"> agent.log 2>&1 &"
            )
            await conn.run(start_cmd, check=False)

        # 7. 等待启动
        await asyncio.sleep(2)

        conn_type = ConnectionType.SSH
        dev_type = DeviceType.SERVER
        if os_type == OSType.WINDOWS:
            dev_type = DeviceType.DESKTOP

        info = AgentInfo(
            agent_id=agent_id,
            name=host.name,
            owner="",
            os_type=os_type,
            os_version=os_version,
            device_type=dev_type,
            connection_type=conn_type,
            host=host.host,
            port=host.port,
            username=host.username,
            deploy_dir=deploy_dir,
            status=AgentStatus.ONLINE,
            agent_port=AGENT_PORT,
            created_at=datetime.now().isoformat(),
            last_seen=datetime.now().isoformat(),
        )
        print(f"[deploy] 完成，Agent ID: {agent_id}")
        return info

    finally:
        conn.close()


async def undeploy(host: RemoteHost):
    """停止并清理 Agent"""
    conn = await _connect(host)
    try:
        os_type, _ = await _detect_os(conn)
        if os_type == OSType.WINDOWS:
            deploy_dir = host.deploy_dir.replace("/", "\\") or "C:\\cyberagentops"
            await conn.run("taskkill /f /im pythonw.exe 2>nul", check=False)
            await conn.run(f'cmd /c "rmdir /s /q {deploy_dir}"', check=False)
        else:
            await conn.run("pkill -f 'agent.py'", check=False)
            await conn.run(f"rm -rf {host.deploy_dir}", check=False)
        print(f"[undeploy] {host.host} Agent 已清理")
    finally:
        conn.close()
