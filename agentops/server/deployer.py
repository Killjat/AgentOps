"""SSH 自动部署 - 检测目标系统 OS，上传对应 Agent，启动服务"""
import asyncio
import uuid
import shutil
import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

import asyncssh

from models import AgentInfo, OSType, AgentStatus, RemoteHost

# Agent 脚本路径（相对于本文件）
AGENT_SCRIPT = Path(__file__).parent.parent / "agent" / "agent.py"
AGENT_PORT = 9000


async def _connect(host: RemoteHost) -> asyncssh.SSHClientConnection:
    """建立 SSH 连接（支持密码或密钥）"""
    kwargs = dict(
        host=host.host,
        port=host.port,
        username=host.username,
        known_hosts=None,          # 不验证 host key（内网场景）
    )
    if host.password:
        kwargs["password"] = host.password
    if host.ssh_key:
        kwargs["client_keys"] = [host.ssh_key]

    return await asyncssh.connect(**kwargs)


async def _detect_os(conn: asyncssh.SSHClientConnection) -> Tuple[OSType, str]:
    """检测目标系统 OS 类型和版本"""
    # 先尝试 uname（Linux/macOS）
    result = await conn.run("uname -s && uname -r", check=False)
    if result.exit_status == 0:
        lines = result.stdout.strip().splitlines()
        kernel = lines[0].lower() if lines else ""
        version = lines[1] if len(lines) > 1 else ""
        if "linux" in kernel:
            # 获取发行版信息
            distro = await conn.run(
                "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
                check=False
            )
            os_ver = distro.stdout.strip() or f"Linux {version}"
            return OSType.LINUX, os_ver
        elif "darwin" in kernel:
            return OSType.MACOS, f"macOS {version}"

    # 尝试 Windows
    result = await conn.run("ver", check=False)
    if result.exit_status == 0 and "windows" in result.stdout.lower():
        return OSType.WINDOWS, result.stdout.strip()

    return OSType.UNKNOWN, "Unknown"


async def _check_python(conn: asyncssh.SSHClientConnection, os_type: OSType) -> str:
    """检测可用的 Python 解释器，返回命令名"""
    candidates = ["python3", "python"] if os_type != OSType.WINDOWS else ["python", "python3"]
    for py in candidates:
        r = await conn.run(f"{py} --version", check=False)
        if r.exit_status == 0:
            return py
    raise RuntimeError("目标系统未安装 Python，请先安装 Python 3.8+")


async def _install_deps(conn: asyncssh.SSHClientConnection, py: str):
    """安装 Agent 依赖"""
    r = await conn.run(
        f"{py} -m pip install aiohttp websockets --quiet --break-system-packages 2>/dev/null "
        f"|| {py} -m pip install aiohttp websockets --quiet",
        check=False
    )
    if r.exit_status != 0:
        # pip 可能不在 PATH，尝试 pip3
        await conn.run(f"pip3 install aiohttp websockets --quiet", check=False)


async def deploy(host: RemoteHost) -> AgentInfo:
    """
    完整部署流程：
    1. SSH 连接
    2. 检测 OS
    3. 上传 agent.py
    4. 安装依赖
    5. 启动 Agent 服务
    6. 返回 AgentInfo
    """
    print(f"[deploy] 连接 {host.username}@{host.host}:{host.port} ...")
    conn = await _connect(host)

    try:
        # 1. 检测 OS
        os_type, os_version = await _detect_os(conn)
        print(f"[deploy] 检测到系统: {os_version} ({os_type})")

        # 2. 检测 Python
        py = await _check_python(conn, os_type)
        print(f"[deploy] Python 解释器: {py}")

        # 3. 创建部署目录
        if os_type == OSType.WINDOWS:
            deploy_dir = host.deploy_dir.replace("/", "\\")
            await conn.run(f"mkdir {deploy_dir} 2>nul", check=False)
        else:
            await conn.run(f"mkdir -p {host.deploy_dir}", check=True)

        # 4. 上传 agent.py
        print(f"[deploy] 上传 agent.py -> {host.deploy_dir}/agent.py ...")
        async with conn.start_sftp_client() as sftp:
            await sftp.put(str(AGENT_SCRIPT), f"{host.deploy_dir}/agent.py")

        # 5. 安装依赖
        print("[deploy] 安装依赖 ...")
        await _install_deps(conn, py)

        # 6. 启动 Agent（后台运行）
        print(f"[deploy] 启动 Agent，监听端口 {AGENT_PORT} ...")
        if os_type == OSType.WINDOWS:
            start_cmd = (
                f"cd /d {deploy_dir} && "
                f"start /b {py} agent.py --port {AGENT_PORT} "
                f"> agent.log 2>&1"
            )
        else:
            start_cmd = (
                f"cd {host.deploy_dir} && "
                f"pkill -f 'agent.py' 2>/dev/null; "
                f"nohup {py} agent.py --port {AGENT_PORT} "
                f"> agent.log 2>&1 &"
            )
        await conn.run(start_cmd, check=False)

        # 7. 等待 Agent 启动
        await asyncio.sleep(2)
        check = await conn.run(
            f"curl -s http://localhost:{AGENT_PORT}/ping 2>/dev/null || "
            f"wget -qO- http://localhost:{AGENT_PORT}/ping 2>/dev/null",
            check=False
        )
        if "pong" not in (check.stdout or "").lower():
            print("[deploy] 警告: Agent 启动确认超时，可能仍在初始化")

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        info = AgentInfo(
            agent_id=agent_id,
            host=host.host,
            port=host.port,
            username=host.username,
            os_type=os_type,
            os_version=os_version,
            deploy_dir=host.deploy_dir,
            status=AgentStatus.ONLINE,
            agent_port=AGENT_PORT,
            created_at=datetime.now().isoformat(),
            last_seen=datetime.now().isoformat(),
        )
        print(f"[deploy] 部署完成，Agent ID: {agent_id}")
        return info

    finally:
        conn.close()


async def undeploy(host: RemoteHost):
    """停止并清理目标机器上的 Agent"""
    conn = await _connect(host)
    try:
        await conn.run("pkill -f 'agent.py'", check=False)
        await conn.run(f"rm -rf {host.deploy_dir}", check=False)
        print(f"[undeploy] {host.host} Agent 已清理")
    finally:
        conn.close()
