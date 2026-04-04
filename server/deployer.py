"""SSH 自动部署 - 检测目标系统 OS，上传 Agent，启动服务"""
import asyncio
import uuid
import os
from datetime import datetime
from pathlib import Path
from typing import Tuple, Callable, Optional

import asyncssh

from models import AgentInfo, OSType, AgentStatus, ConnectionType, DeviceType, RemoteHost

AGENT_DIR = Path(__file__).parent.parent / "agent"
AGENT_PORT = 9000
AGENT_FILES = ["agent.py", "base.py", "linux.py", "windows.py", "__main__.py", "__init__.py"]

# 日志回调类型：接收一行日志字符串
LogFn = Callable[[str], None]


def _noop(msg: str): pass


async def _connect(host: RemoteHost) -> asyncssh.SSHClientConnection:
    kwargs = dict(host=host.host, port=host.port, username=host.username, known_hosts=None)
    if host.password:
        kwargs["password"] = host.password
        kwargs["preferred_auth"] = "password,keyboard-interactive"
    if host.ssh_key:
        kwargs["client_keys"] = [host.ssh_key]
    return await asyncssh.connect(**kwargs)


async def _detect_os(conn, log: LogFn) -> Tuple[OSType, str]:
    log("▶ 检测操作系统...")
    try:
        r = await conn.run("uname -s && uname -r", check=False, encoding="utf-8", errors="replace", timeout=10)
        if r.exit_status == 0 and r.stdout:
            lines = r.stdout.strip().splitlines()
            kernel = lines[0].lower()
            if "linux" in kernel:
                d = await conn.run(
                    "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
                    check=False, encoding="utf-8", errors="replace", timeout=10
                )
                os_ver = d.stdout.strip() or f"Linux {lines[1] if len(lines) > 1 else ''}"
                log(f"  系统: {os_ver}")
                return OSType.LINUX, os_ver
            elif "darwin" in kernel:
                os_ver = f"macOS {lines[1] if len(lines) > 1 else ''}"
                log(f"  系统: {os_ver}")
                return OSType.MACOS, os_ver
    except Exception:
        pass
    # Windows
    for enc in ["gbk", "latin-1"]:
        try:
            r = await conn.run("ver", check=False, encoding=enc, errors="replace", timeout=10)
            if r.exit_status == 0 and r.stdout and ("windows" in r.stdout.lower() or "microsoft" in r.stdout.lower()):
                os_ver = r.stdout.strip()[:80]
                log(f"  系统: {os_ver}")
                return OSType.WINDOWS, os_ver
        except Exception:
            pass
    log("  ⚠️ 无法识别操作系统，默认 UNKNOWN")
    return OSType.UNKNOWN, "Unknown"


async def _check_python(conn, os_type: OSType, log: LogFn) -> str:
    log("▶ 检测 Python 环境...")
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    # Linux/macOS 优先找 python3，Windows 优先找 python
    candidates = ["python", "python3"] if os_type == OSType.WINDOWS else ["python3", "python3.11", "python3.10", "python3.9", "python3.8", "python3.6"]
    for py in candidates:
        r = await conn.run(f"{py} --version", check=False, encoding=enc, errors="replace", timeout=10)
        if r.exit_status == 0:
            ver = (r.stdout or r.stderr or "").strip()
            # Linux 上必须是 python3
            if os_type != OSType.WINDOWS and "python 2" in ver.lower():
                continue
            log(f"  Python: {ver} ({py})")
            return py

    # 没找到 python3，尝试安装
    if os_type != OSType.WINDOWS:
        log("  ⚠️ 未找到 Python 3，尝试安装...")
        await conn.run(
            "yum install -y python3 2>/dev/null || apt-get install -y python3 2>/dev/null || true",
            check=False, encoding=enc, errors="replace", timeout=180
        )
        # 升级 pip（CentOS 7 自带 pip 9 太旧，装不了 aiohttp）
        await conn.run(
            "python3 -m pip install --upgrade pip 2>/dev/null || true",
            check=False, encoding=enc, errors="replace", timeout=60
        )
        for py in ["python3", "python3.6"]:
            r = await conn.run(f"{py} --version", check=False, encoding=enc, errors="replace", timeout=10)
            if r.exit_status == 0:
                ver = (r.stdout or r.stderr or "").strip()
                log(f"  ✅ 安装成功: {ver} ({py})")
                return py

    raise RuntimeError("目标系统未安装 Python 3，且自动安装失败，请手动安装 Python 3")


async def _precheck(conn, py: str, os_type: OSType, log: LogFn) -> dict:
    """安装前预检：aiohttp 是否已装、端口是否占用"""
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    result = {}

    log("▶ 预检: 检测 aiohttp/websockets 是否已安装...")
    r = await conn.run(f"{py} -c \"import aiohttp, websockets; print(aiohttp.__version__)\"",
                       check=False, encoding=enc, errors="replace", timeout=10)
    if r.exit_status == 0 and r.stdout.strip():
        result["aiohttp"] = r.stdout.strip()
        log(f"  ✅ aiohttp {r.stdout.strip()} + websockets 已安装，跳过安装")
    else:
        result["aiohttp"] = None
        log("  ℹ️ 依赖未完整安装，将自动安装")

    log(f"▶ 预检: 检测端口 {AGENT_PORT} 占用情况...")
    if os_type == OSType.WINDOWS:
        r = await conn.run(f"netstat -ano | findstr :{AGENT_PORT}",
                           check=False, encoding=enc, errors="replace", timeout=10)
    else:
        r = await conn.run(f"lsof -ti:{AGENT_PORT} 2>/dev/null || ss -tlnp 2>/dev/null | grep :{AGENT_PORT} || true",
                           check=False, encoding=enc, errors="replace", timeout=10)
    if r.stdout and r.stdout.strip():
        result["port_in_use"] = True
        log(f"  ⚠️ 端口 {AGENT_PORT} 已被占用，将在部署时强制释放")
    else:
        result["port_in_use"] = False
        log(f"  ✅ 端口 {AGENT_PORT} 空闲")

    return result


async def _upload_files(conn, deploy_dir: str, os_type: OSType, log: LogFn):
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    log("▶ 停止旧 Agent 进程...")
    if os_type == OSType.WINDOWS:
        await conn.run("taskkill /f /im python.exe 2>nul", check=False, encoding=enc, errors="replace", timeout=10)
    else:
        await conn.run(f"pkill -f 'agent.py' 2>/dev/null; lsof -ti:{AGENT_PORT} | xargs kill -9 2>/dev/null; true",
                       check=False, encoding=enc, errors="replace", timeout=10)
    await asyncio.sleep(1)

    if os_type == OSType.WINDOWS:
        await conn.run(f'cmd /c "if exist {deploy_dir}\\__pycache__ rmdir /s /q {deploy_dir}\\__pycache__"',
                       check=False, encoding=enc, errors="replace", timeout=10)
    else:
        await conn.run(f"rm -rf {deploy_dir}/__pycache__ {deploy_dir}/*.pyc 2>/dev/null; true",
                       check=False, encoding=enc, errors="replace", timeout=10)

    log(f"▶ 上传 Agent 文件到 {deploy_dir}...")
    async with conn.start_sftp_client() as sftp:
        for fname in AGENT_FILES:
            local = AGENT_DIR / fname
            if not local.exists():
                continue
            sep = "\\" if os_type == OSType.WINDOWS else "/"
            remote = f"{deploy_dir}{sep}{fname}"
            await sftp.put(str(local), remote)
            log(f"  上传 {fname} ✓")


async def _install_deps(conn, py: str, os_type: OSType, precheck: dict, log: LogFn):
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    if precheck.get("aiohttp"):
        log("▶ 依赖已满足，跳过安装")
        return
    log("▶ 安装依赖 aiohttp websockets...")
    if os_type == OSType.WINDOWS:
        r = await conn.run(f"{py} -m pip install aiohttp websockets --quiet",
                           check=False, encoding=enc, errors="replace", timeout=120)
    else:
        await conn.run(f"{py} -m ensurepip --upgrade 2>/dev/null || true",
                       check=False, encoding=enc, errors="replace", timeout=30)
        r = await conn.run(
            f"{py} -m pip install aiohttp websockets --quiet --break-system-packages 2>/dev/null "
            f"|| {py} -m pip install aiohttp websockets --quiet",
            check=False, encoding=enc, errors="replace", timeout=120
        )
    if r.exit_status == 0:
        log("  ✅ 依赖安装成功")
    else:
        log(f"  ⚠️ 依赖安装可能失败: {(r.stderr or '')[:200]}")


async def _start_agent(conn, deploy_dir: str, py: str, agent_id: str, os_type: OSType, log: LogFn):
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    server_url = os.getenv("SERVER_URL", "")
    extra = f"--agent-id {agent_id} --type {'windows' if os_type == OSType.WINDOWS else 'linux'}"
    if server_url:
        extra += f" --server {server_url}"

    # 确保用 python3 启动
    if os_type != OSType.WINDOWS:
        for candidate in ["python3", "python3.8", "python3.9", "python3.11", py]:
            r = await conn.run(f"{candidate} --version", check=False, encoding=enc, errors="replace", timeout=5)
            if r.exit_status == 0 and "python 3" in (r.stdout or r.stderr or "").lower():
                py = candidate
                break

    log(f"▶ 启动 Agent (ID: {agent_id}, Python: {py})...")
    if os_type == OSType.WINDOWS:
        log_file = f"{deploy_dir}\\agent.log"
        cmd = 'cmd /c "cd /d ' + deploy_dir + ' && python -u agent.py --port ' + str(AGENT_PORT) + ' ' + extra + ' > ' + log_file + ' 2>&1"'
        proc = await conn.create_process(cmd, encoding=enc, errors="replace")
        await asyncio.sleep(1)
        proc.close()
    else:
        # setsid 创建新 session，进程完全脱离 SSH channel，避免 asyncssh 等待
        cmd = (f"setsid python3 -u {deploy_dir}/agent.py --port {AGENT_PORT} {extra} "
               f"> {deploy_dir}/agent.log 2>&1 < /dev/null &")
        proc = await conn.create_process(cmd, encoding=enc, errors="replace")
        await asyncio.sleep(0.5)
        proc.close()


async def _verify(conn, deploy_dir: str, os_type: OSType, log: LogFn) -> bool:
    """验证 Agent 是否启动：检查进程是否存在"""
    enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
    log("▶ 等待 Agent 启动...")
    await asyncio.sleep(3)

    log("▶ 验证 Agent 进程...")
    if os_type == OSType.WINDOWS:
        r = await conn.run("tasklist | findstr python", check=False, encoding=enc, errors="replace", timeout=10)
        if r.stdout and "python" in r.stdout.lower():
            log("  ✅ Agent 进程运行中（WebSocket 连接将在几秒内建立）")
            return True
    else:
        r = await conn.run(f"pgrep -f 'agent.py' && echo running || echo stopped",
                           check=False, encoding=enc, errors="replace", timeout=10)
        if "running" in (r.stdout or ""):
            log("  ✅ Agent 进程运行中（WebSocket 连接将在几秒内建立）")
            return True

    log("  ⚠️ Agent 进程未找到，读取 agent.log...")
    if os_type == OSType.WINDOWS:
        log_cmd = f"type {deploy_dir}\\agent.log 2>nul"
    else:
        log_cmd = f"tail -20 {deploy_dir}/agent.log 2>/dev/null"
    log_r = await conn.run(log_cmd, check=False, encoding=enc, errors="replace", timeout=10)
    for line in (log_r.stdout or "(空)").splitlines()[-10:]:
        log(f"  {line}")
    return False


async def deploy(host: RemoteHost, log: LogFn = _noop) -> AgentInfo:
    log(f"▶ 连接 {host.username}@{host.host}:{host.port}...")
    conn = await _connect(host)
    try:
        os_type, os_version = await _detect_os(conn, log)
        py = await _check_python(conn, os_type, log)

        enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
        if os_type == OSType.WINDOWS:
            deploy_dir = (host.deploy_dir or "C:\\agentops").replace("/", "\\").replace("\\\\", "\\")
            await conn.run(f'cmd /c "if not exist {deploy_dir} mkdir {deploy_dir}"',
                           check=False, encoding=enc, errors="replace", timeout=10)
        else:
            deploy_dir = host.deploy_dir or "/opt/agentops"
            await conn.run(f"mkdir -p {deploy_dir}", check=False, encoding=enc, errors="replace", timeout=10)

        precheck = await _precheck(conn, py, os_type, log)
        await _upload_files(conn, deploy_dir, os_type, log)
        await _install_deps(conn, py, os_type, precheck, log)

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        await _start_agent(conn, deploy_dir, py, agent_id, os_type, log)
        verified = await _verify(conn, deploy_dir, os_type, log)
        status = AgentStatus.ONLINE if verified else AgentStatus.OFFLINE

        info = AgentInfo(
            agent_id=agent_id, name=host.name, owner="",
            os_type=os_type, os_version=os_version,
            device_type=DeviceType.DESKTOP if os_type == OSType.WINDOWS else DeviceType.SERVER,
            connection_type=ConnectionType.SSH,
            agent_deploy_dir=deploy_dir, status=status,
            agent_port=AGENT_PORT,
            created_at=datetime.now().isoformat(),
            last_seen=datetime.now().isoformat(),
        )
        log(f"{'✅ 部署完成' if verified else '⚠️ 部署完成但 Agent 未响应'}，Agent ID: {agent_id}")
        return info
    finally:
        conn.close()


async def update(host: RemoteHost, agent_id: str, log: LogFn = _noop) -> dict:
    log(f"▶ 连接 {host.username}@{host.host}:{host.port}...")
    conn = await _connect(host)
    try:
        os_type, _ = await _detect_os(conn, log)
        py = await _check_python(conn, os_type, log)
        enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"

        deploy_dir = (host.deploy_dir or "C:\\agentops").replace("/", "\\").replace("\\\\", "\\") \
            if os_type == OSType.WINDOWS else (host.deploy_dir or "/opt/agentops")

        precheck = await _precheck(conn, py, os_type, log)
        await _upload_files(conn, deploy_dir, os_type, log)
        await _install_deps(conn, py, os_type, precheck, log)
        await _start_agent(conn, deploy_dir, py, agent_id, os_type, log)

        verified = await _verify(conn, deploy_dir, os_type, log)
        if not verified:
            log(f"⚠️ Agent 启动验证失败，请检查 {deploy_dir}/agent.log")
        return {"status": "success" if verified else "warning", "agent_id": agent_id}
    finally:
        conn.close()


async def undeploy(host: RemoteHost, log: LogFn = _noop):
    log(f"▶ 连接 {host.username}@{host.host}:{host.port}...")
    conn = await _connect(host)
    try:
        os_type, _ = await _detect_os(conn, log)
        enc = "latin-1" if os_type == OSType.WINDOWS else "utf-8"
        log("▶ 停止 Agent 并清理文件...")
        if os_type == OSType.WINDOWS:
            deploy_dir = (host.deploy_dir or "C:\\agentops").replace("/", "\\").replace("\\\\", "\\")
            await conn.run("taskkill /f /im python.exe 2>nul", check=False, encoding=enc, errors="replace", timeout=10)
            await conn.run(f'cmd /c "rmdir /s /q {deploy_dir}"', check=False, encoding=enc, errors="replace", timeout=15)
        else:
            await conn.run("pkill -f 'agent.py' 2>/dev/null; true", check=False, encoding=enc, errors="replace", timeout=10)
            await conn.run(f"rm -rf {host.deploy_dir}", check=False, encoding=enc, errors="replace", timeout=15)
        log(f"✅ {host.host} 已清理")
    finally:
        conn.close()
