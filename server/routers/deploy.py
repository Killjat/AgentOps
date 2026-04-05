"""应用部署路由"""
import asyncio
import json
import re as _re
import uuid
from datetime import datetime
from typing import List, Optional

import asyncssh
from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from models import AppDeployRequest, AppDeployResult, AppDeployStatus, ChatRequest
from core.state import agents, app_deploys, servers
from core.storage import _append_deploy_log, _save_app_deploys
from routers.auth import _check_owner, _check_perm, _get_caller, _is_admin
from routers.agents import _agent_exec, _get_agent, _ssh_kwargs, _ws_call
import llm as LLM

router = APIRouter(prefix="/deploy", tags=["deploy"])


@router.post("/app/precheck")
async def precheck_deploy(request: AppDeployRequest,
                          authorization: str = Header(default="")):
    """部署前检查：端口占用、已有部署、依赖环境"""
    _check_perm(authorization, "login")
    info = _get_agent(request.agent_id)
    _check_owner(authorization, info.owner, "Agent")

    results = {}
    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            async def check(cmd):
                r = await conn.run(cmd, check=False)
                return (r.stdout or r.stderr or "").strip()

            existing = await check(
                f"test -d {request.deploy_dir}/.git && git -C {request.deploy_dir} log -1 --oneline 2>/dev/null || echo 'not_exists'"
            )
            if "not_exists" in existing:
                results["existing_deploy"] = {"status": "none", "message": "目录不存在，将进行首次部署"}
            else:
                results["existing_deploy"] = {"status": "found", "message": f"已有部署: {existing}"}

            port = 8000
            port_check = await check(f"ss -tlnp | grep ':{port}' | head -3")
            if port_check:
                results["port"] = {"status": "occupied", "message": f"端口 {port} 已被占用: {port_check[:100]}"}
            else:
                results["port"] = {"status": "free", "message": f"端口 {port} 空闲"}

            py_ver = await check("python3 --version 2>/dev/null || echo 'not_found'")
            pip_ver = await check("pip3 --version 2>/dev/null || python3 -m pip --version 2>/dev/null || echo 'not_found'")
            results["python"] = {
                "status": "ok" if "not_found" not in py_ver else "missing",
                "message": py_ver if "not_found" not in py_ver else "未安装 Python3"
            }
            results["pip"] = {
                "status": "ok" if "not_found" not in pip_ver else "missing",
                "message": pip_ver[:60] if "not_found" not in pip_ver else "未安装 pip，部署时会自动安装"
            }

            git_ver = await check("git --version 2>/dev/null || echo 'not_found'")
            results["git"] = {
                "status": "ok" if "not_found" not in git_ver else "missing",
                "message": git_ver if "not_found" not in git_ver else "未安装 git，部署时会自动安装"
            }

            disk = await check(f"df -h {request.deploy_dir} 2>/dev/null | tail -1 || df -h / | tail -1")
            results["disk"] = {"status": "ok", "message": disk}

            if "found" in results.get("existing_deploy", {}).get("status", ""):
                has_deploy_sh = await check(f"test -f {request.deploy_dir}/deploy.sh && echo yes || echo no")
                results["deploy_sh"] = {
                    "status": "found" if "yes" in has_deploy_sh else "none",
                    "message": "仓库包含 deploy.sh，将直接执行" if "yes" in has_deploy_sh else "无 deploy.sh，将自动分析部署"
                }
        finally:
            conn.close()
    except Exception as e:
        results["error"] = {"status": "error", "message": str(e)}

    return results


@router.get("/app/{deploy_id}/stream")
async def stream_deploy_log(deploy_id: str, authorization: str = ""):
    """SSE 实时推送部署日志"""
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")

    async def event_generator():
        last_len = 0
        for _ in range(300):
            if deploy_id in app_deploys:
                d = app_deploys[deploy_id]
                current_log = d.log or ""
                if len(current_log) > last_len:
                    new_content = current_log[last_len:]
                    for line in new_content.splitlines():
                        yield f"data: {line}\n\n"
                    last_len = len(current_log)
                if d.status in ("success", "failed"):
                    yield f"data: __STATUS__{d.status}\n\n"
                    break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/app", response_model=AppDeployResult)
async def create_app_deploy(request: AppDeployRequest, background_tasks: BackgroundTasks,
                            authorization: str = Header(default="")):
    """创建应用部署任务"""
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)
    info = _get_agent(request.agent_id)
    _check_owner(authorization, info.owner, "Agent")

    deploy_id = uuid.uuid4().hex[:12]
    result = AppDeployResult(
        deploy_id=deploy_id,
        agent_id=request.agent_id,
        owner=caller,
        repo_url=request.repo_url,
        deploy_dir=request.deploy_dir,
        status=AppDeployStatus.PENDING,
        created_at=datetime.now().isoformat(),
    )
    app_deploys[deploy_id] = result
    _save_app_deploys()
    background_tasks.add_task(_run_app_deploy, deploy_id, request)
    return result


@router.post("/app/{deploy_id}/upload")
async def upload_config_file(deploy_id: str,
                             file: UploadFile = File(...),
                             remote_path: str = "",
                             as_env: bool = False,
                             authorization: str = Header(default="")):
    """上传配置文件，as_env=true 时自动保存为 .env"""
    _check_perm(authorization, "login")
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")

    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")
    info = _get_agent(d.agent_id)

    content = await file.read()
    target = f"{d.deploy_dir}/.env" if as_env else (remote_path or f"{d.deploy_dir}/{file.filename}")

    try:
        conn = await asyncssh.connect(**_ssh_kwargs(info))
        try:
            dir_path = "/".join(target.split("/")[:-1])
            await conn.run(f"mkdir -p {dir_path}", check=False)
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(target, 'wb') as f_remote:
                    await f_remote.write(content)
        finally:
            conn.close()
        return {"ok": True, "path": target, "size": len(content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/app/{deploy_id}", response_model=AppDeployResult)
async def get_app_deploy(deploy_id: str, authorization: str = Header(default="")):
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")
    return d


@router.get("/app")
async def list_app_deploys(authorization: str = Header(default="")):
    result = list(app_deploys.values())
    if not _is_admin(authorization):
        caller = _get_caller(authorization)
        result = [d for d in result if d.owner == caller]
    return sorted(result, key=lambda d: d.created_at, reverse=True)


@router.post("/scan/{agent_id}")
async def scan_agent_apps(agent_id: str, authorization: str = Header(default="")):
    """扫描 Agent 服务器上的已部署应用"""
    _check_perm(authorization, "login")
    info = _get_agent(agent_id)

    try:
        from core.state import _ws_connections
        if agent_id not in _ws_connections:
            raise HTTPException(status_code=503, detail="Agent 未连接，请等待 Agent 上线后重试")

        resp = await _ws_call(agent_id, {"type": "discover"}, timeout=60)
        agent_data = resp.get("data", {})

        discovered = []
        for svc in agent_data.get("services", []):
            if svc.get("port"):
                discovered.append({"type": "service", "name": svc["name"],
                                    "description": svc.get("description", ""),
                                    "port": svc["port"], "status": svc["status"]})
        for container in agent_data.get("containers", []):
            if container.get("port"):
                discovered.append({"type": "container", "name": container["name"],
                                    "description": f"Docker: {container.get('status', '')}",
                                    "port": container["port"], "status": "running"})
        for port_info in agent_data.get("ports", []):
            discovered.append({"type": "port", "name": f"Port {port_info['port']}",
                                "description": f"Process: {port_info.get('process', 'unknown')}",
                                "port": port_info["port"], "status": "listening"})

        tools = agent_data.get("tools", [])

        return {"agent_id": agent_id, "hostname": agent_data.get("hostname", ""),
                "discovered": discovered, "count": len(discovered),
                "tools": tools, "tools_count": len(tools)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@router.post("/register/{agent_id}")
async def register_discovered_app(agent_id: str, req: dict, authorization: str = Header(default="")):
    """将发现的应用注册为技能"""
    _check_perm(authorization, "login")
    _get_agent(agent_id)

    name = req.get("name", "")
    app_type = req.get("type", "")
    port = req.get("port", "")
    description = req.get("description", "")

    if not name or not port:
        raise HTTPException(status_code=400, detail="name 和 port 是必填项")

    deploy_id = f"app-{uuid.uuid4().hex[:8]}"
    deploy_dir = f"/opt/{name.split('.')[0]}"

    app_deploy = AppDeployResult(
        deploy_id=deploy_id,
        agent_id=agent_id,
        owner=_get_caller(authorization),
        repo_url=f"{app_type}://{name}",
        deploy_dir=deploy_dir,
        status=AppDeployStatus.SUCCESS,
        log=f"手动注册的 {app_type} 应用: {name}\n描述: {description}\n端口: {port}",
        created_at=datetime.now().isoformat()
    )

    app_deploys[deploy_id] = app_deploy
    _save_app_deploys()
    return app_deploy


@router.get("/app/{deploy_id}/log")
async def get_deploy_log(deploy_id: str, authorization: str = Header(default="")):
    """获取部署的详细日志文件"""
    from core.storage import LOGS_DIR
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")

    log_file = LOGS_DIR / f"{deploy_id}.log"
    if not log_file.exists():
        return {"log": d.log or "无详细日志", "file_exists": False}

    try:
        log_content = log_file.read_text(encoding='utf-8')
        return {"log": log_content, "file_exists": True}
    except Exception as e:
        return {"log": f"读取日志失败: {e}", "file_exists": False, "inline_log": d.log}


@router.post("/app/{deploy_id}/chat")
async def chat_with_deploy(deploy_id: str, req: ChatRequest,
                           authorization: str = Header(default="")):
    """基于部署结果继续对话，可执行命令或上传文件"""
    _check_perm(authorization, "login")
    if deploy_id not in app_deploys:
        raise HTTPException(status_code=404, detail="部署任务不存在")
    d = app_deploys[deploy_id]
    _check_owner(authorization, d.owner, "部署任务")
    info = _get_agent(d.agent_id)

    if not hasattr(d, 'conversation'):
        d.__dict__['conversation'] = []
    conv = d.__dict__.get('conversation', [])

    server_host = servers[info.server_id].host if info.server_id in servers else info.name
    context = f"""你是一个 Linux 运维和应用部署专家。
已部署的仓库：{d.repo_url}
部署目录：{d.deploy_dir}
目标服务器：{server_host} ({info.os_version})
部署状态：{d.status}
部署日志：
{d.log[-1000:] if d.log else '无'}
"""
    if req.execute:
        exec_system = context + "\n用户想执行操作，请只返回一条可直接执行的 shell 命令，不要任何解释，不要 markdown 格式。"
        cmd_messages = [{"role": "system", "content": exec_system}]
        for msg in conv:
            if msg["role"] != "system":
                cmd_messages.append({"role": msg["role"], "content": msg["content"]})
        cmd_messages.append({"role": "user", "content": req.message})

        command = await LLM.chat(cmd_messages, max_tokens=200)
        command = command.strip().strip('`').strip()
        if command.startswith("bash\n") or command.startswith("sh\n"):
            command = command.split("\n", 1)[1].strip()

        exec_result = await _agent_exec(info, command, 120)
        success = exec_result.get("success", False)
        output = exec_result.get("output") or exec_result.get("error", "")

        analysis_messages = [{"role": "system", "content": context}]
        for msg in conv:
            if msg["role"] != "system":
                analysis_messages.append({"role": msg["role"], "content": msg["content"]})
        analysis_messages += [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": f"执行命令：`{command}`"},
            {"role": "user", "content": f"命令执行{'成功' if success else '失败'}，输出：\n{output[:1000]}\n\n请分析结果。"}
        ]
        reply = await LLM.chat(analysis_messages, max_tokens=500)

        conv.append({"role": "user", "content": req.message})
        conv.append({"role": "assistant", "content": f"执行命令：`{command}`\n\n{reply}"})
        d.__dict__['conversation'] = conv
        _save_app_deploys()

        return {"reply": reply, "command": command, "exec_result": exec_result, "conversation": conv}
    else:
        messages = [{"role": "system", "content": context}]
        for msg in conv:
            if msg["role"] != "system":
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": req.message})
        reply = await LLM.chat(messages, max_tokens=600)
        conv.append({"role": "user", "content": req.message})
        conv.append({"role": "assistant", "content": reply})
        d.__dict__['conversation'] = conv
        _save_app_deploys()
        return {"reply": reply, "command": None, "exec_result": None, "conversation": conv}


async def _run_app_deploy(deploy_id: str, request: AppDeployRequest):
    """后台执行应用部署 - AI 智能分析 + 验证"""
    d = app_deploys[deploy_id]
    info = agents[request.agent_id]
    log_lines = []

    def log(msg: str):
        log_lines.append(msg)
        d.log = "\n".join(log_lines)
        print(f"[app-deploy] {msg}")

    try:
        d.status = AppDeployStatus.RUNNING
        conn = await asyncssh.connect(**_ssh_kwargs(info))

        try:
            async def run(cmd, timeout=120):
                r = await asyncio.wait_for(conn.run(cmd, check=False), timeout=timeout)
                out = (r.stdout or r.stderr or "").strip()
                if out:
                    log(out[-1000:])
                return r

            # ── 1. 安装 git ──────────────────────────────────────
            log(f"▶ 开始部署 {request.repo_url}")
            git_check = await conn.run("which git", check=False)
            if git_check.exit_status != 0:
                log("安装 git...")
                await run("apt-get install -y git 2>/dev/null || dnf install -y git 2>/dev/null || yum install -y git 2>/dev/null")

            # ── 2. clone / pull ───────────────────────────────────
            check_dir = await conn.run(f"test -d {request.deploy_dir}/.git && echo exists", check=False)
            is_update = "exists" in (check_dir.stdout or "")

            if is_update:
                git_remote = await conn.run(f"cd {request.deploy_dir} && git remote get-url origin 2>/dev/null", check=False)
                current_repo = (git_remote.stdout or "").strip()

                def normalize_repo_url(url):
                    url = url.rstrip('/')
                    if url.endswith('.git'):
                        url = url[:-4]
                    url = url.replace('git@github.com:', 'https://github.com/')
                    return url.lower()

                if normalize_repo_url(current_repo) != normalize_repo_url(request.repo_url):
                    log(f"⚠️  配置目录 {request.deploy_dir} 已有其他项目: {current_repo}")
                    log(f"▶ 新项目: {request.repo_url}")
                    log(f"▶ 将清空目录并重新 clone")
                    await run(f"rm -rf {request.deploy_dir}")
                    is_update = False

            if is_update:
                log(f"▶ 检测到已有部署，执行更新 (git pull {request.branch})")
                await run(f"cd {request.deploy_dir} && git fetch origin && git checkout {request.branch} && git pull origin {request.branch}")
            else:
                log(f"▶ 首次部署，git clone -> {request.deploy_dir}")
                await run(f"mkdir -p {request.deploy_dir}")
                await run(f"git clone -b {request.branch} {request.repo_url} {request.deploy_dir}", timeout=180)

            # ── 3. AI 分析仓库，生成部署计划 ─────────────────────
            log("▶ AI 分析仓库结构...")
            repo_info = await conn.run(
                f"ls -la {request.deploy_dir}/ && echo '===' && "
                f"cat {request.deploy_dir}/requirements.txt 2>/dev/null && echo '===' && "
                f"cat {request.deploy_dir}/package.json 2>/dev/null && echo '===' && "
                f"cat {request.deploy_dir}/Dockerfile 2>/dev/null && echo '===' && "
                f"cat {request.deploy_dir}/README.md 2>/dev/null | head -30",
                check=False
            )
            sys_info = await conn.run(
                "python3 --version 2>/dev/null; node --version 2>/dev/null; "
                "java -version 2>/dev/null; docker --version 2>/dev/null; "
                "which pip3 2>/dev/null; which npm 2>/dev/null",
                check=False
            )

            plan_prompt = f"""你是一个 DevOps 专家，分析以下仓库信息，生成部署计划。

仓库：{request.repo_url}
目标服务器系统：{info.os_version}
服务器已安装工具：
{(sys_info.stdout or '').strip()}

仓库文件结构和关键文件：
{(repo_info.stdout or '')[:3000]}

请生成一个 JSON 格式的部署计划：
{{
  "project_type": "python/node/java/docker/other",
  "description": "项目简介",
  "install_steps": ["步骤1命令", "步骤2命令"],
  "start_cmd": "启动命令",
  "health_check": "验证是否启动成功的命令",
  "expected_port": 8000,
  "warnings": ["注意事项1", "注意事项2"],
  "suggestions": ["建议1", "建议2"]
}}

只返回 JSON，不要其他内容。"""

            plan_raw = await LLM.chat([{"role": "user", "content": plan_prompt}], max_tokens=600)
            plan = {}
            try:
                json_match = _re.search(r'\{.*\}', plan_raw, _re.DOTALL)
                if json_match:
                    plan = json.loads(json_match.group())
            except Exception:
                pass

            log(f"\n── AI 部署分析 ──")
            log(f"项目类型: {plan.get('project_type', '未知')}")
            log(f"项目描述: {plan.get('description', '未知')}")
            if plan.get('warnings'):
                log(f"⚠️  注意: {'; '.join(plan.get('warnings', []))}")
            if plan.get('suggestions'):
                log(f"💡 建议: {'; '.join(plan.get('suggestions', []))}")
            log("─────────────────")

            # ── 4. 检查并关闭现有服务 ────────────────────────────
            log("▶ 检测并关闭现有服务...")
            expected_port = plan.get('expected_port', 8000)

            log("  检查 systemd 服务...")
            svc_name = request.service_name or request.repo_url.split("/")[-1].replace(".", "")
            service_check = await conn.run(
                f"systemctl list-units --all | grep '{svc_name}' | awk '{{print $1}}'",
                check=False
            )

            if service_check.stdout and service_check.stdout.strip():
                services = [s.strip() for s in service_check.stdout.split('\n') if s.strip() and '.service' in s]
                for svc in services:
                    log(f"  ⚠️  发现 systemd 服务: {svc}")
                    await run(f"systemctl stop {svc} 2>/dev/null || true", timeout=15)
                    await run(f"systemctl disable {svc} 2>/dev/null || true", timeout=10)
                    log(f"  ✅ 已停止并禁用服务: {svc}")
                await asyncio.sleep(3)

            port_check = await conn.run(
                f"lsof -ti:{expected_port} 2>/dev/null || ss -tlnp | grep ':{expected_port}' | awk '{{print $7}}' | cut -d, -f2 | cut -d= -f2",
                check=False
            )

            if port_check.stdout and port_check.stdout.strip():
                pids = port_check.stdout.strip().split()
                log(f"  ⚠️  发现端口 {expected_port} 被进程占用: {pids}")
                await run(f"fuser -k {expected_port}/tcp 2>/dev/null || kill -9 {' '.join(pids)} 2>/dev/null || true", timeout=10)
                log("  ✅ 已通过端口关闭服务")
                await asyncio.sleep(3)

            project_type = plan.get('project_type', '')
            if project_type == 'python':
                proc_patterns = [f"python.*{request.deploy_dir}", "python.*main.py", "uvicorn.*main:app"]
            elif project_type == 'node':
                proc_patterns = [f"node.*{request.deploy_dir}", "npm.*start"]
            elif project_type == 'java':
                proc_patterns = [f"java.*{request.deploy_dir}", "java.*-jar"]
            else:
                proc_patterns = []

            for pattern in proc_patterns:
                proc_check = await conn.run(f"pgrep -f '{pattern}'", check=False)
                if proc_check.stdout and proc_check.stdout.strip():
                    log(f"  ⚠️  发现匹配进程: {pattern}")
                    await run(f"pkill -f '{pattern}' 2>/dev/null || true", timeout=10)
                    log("  ✅ 已关闭相关进程")
                    await asyncio.sleep(2)

            # ── 5. 检查 deploy.sh ────────────────────────────────
            deploy_sh = await conn.run(
                f"test -f {request.deploy_dir}/deploy.sh && echo exists || echo no", check=False
            )
            output = ""
            if "exists" in (deploy_sh.stdout or ""):
                log("▶ 检测到 deploy.sh，直接执行...")
                r = await conn.run(
                    f"cd {request.deploy_dir} && chmod +x deploy.sh && bash deploy.sh 2>&1",
                    check=False
                )
                output = (r.stdout or r.stderr or "").strip()
                if output:
                    log(output[-2000:])

                if r.exit_status != 0 or ("error" in output.lower() or "failed" in output.lower() or "no such file" in output.lower()):
                    log("▶ deploy.sh 执行遇到问题，AI 分析中...")
                    analysis_prompt = f"""分析以下 deploy.sh 执行输出，判断是否需要修改脚本：

目标系统：{info.os_version}
deploy.sh 执行结果：
{output[-1500:]}

如果执行失败，请：
1. 指出具体错误原因（如路径错误、权限问题、依赖缺失等）
2. 提供具体的修复建议，包括需要修改的代码片段
3. 如果脚本中包含硬编码的路径（如 /etc/nginx/sites-available/），需要适配不同系统

如果执行成功，则回答："脚本执行正常"

回答格式：
问题：[具体问题]
原因：[原因分析]
修复建议：[具体修改建议]"""

                    try:
                        analysis = await LLM.chat([{"role": "user", "content": analysis_prompt}], max_tokens=500)
                        log(f"\n── deploy.sh 问题分析 ──\n{analysis}\n─────────────────")
                    except Exception as e:
                        log(f"⚠️  AI 分析失败: {e}")
            else:
                # ── 6. 按 AI 计划安装依赖 ────────────────────────
                install_steps = plan.get('install_steps') or []
                if request.install_cmd:
                    install_steps = [request.install_cmd]
                elif not install_steps:
                    if (await conn.run(f"test -f {request.deploy_dir}/requirements.txt", check=False)).exit_status == 0:
                        install_steps = ["pip3 install -r requirements.txt 2>/dev/null || python3 -m pip install -r requirements.txt --break-system-packages"]

                for step in install_steps:
                    log(f"▶ {step}")
                    await run(f"cd {request.deploy_dir} && {step}", timeout=300)

                # ── 7. 启动或重启 ─────────────────────────────────
                start_cmd = request.start_cmd or plan.get('start_cmd', '')
                if request.use_systemd and request.service_name and start_cmd:
                    if is_update:
                        log(f"▶ 更新：重启 systemd 服务 {request.service_name}")
                        await run(f"systemctl restart {request.service_name}")
                    else:
                        log(f"▶ 注册 systemd 服务: {request.service_name}")
                        svc = (
                            f"[Unit]\nDescription={request.service_name}\nAfter=network.target\n\n"
                            f"[Service]\nType=simple\nWorkingDirectory={request.deploy_dir}\n"
                            f"ExecStart={start_cmd}\nRestart=always\nRestartSec=5\n\n"
                            f"[Install]\nWantedBy=multi-user.target\n"
                        )
                        async with conn.start_sftp_client() as sftp:
                            async with sftp.open(f"/etc/systemd/system/{request.service_name}.service", 'w') as f:
                                await f.write(svc)
                        await run(f"systemctl daemon-reload && systemctl enable {request.service_name} && systemctl restart {request.service_name}")
                elif start_cmd:
                    if is_update:
                        old_proc = start_cmd.split()[0]
                        log(f"▶ 更新：重启应用进程")
                        await run(f"pkill -f '{old_proc}' 2>/dev/null; sleep 1; cd {request.deploy_dir} && nohup {start_cmd} > app.log 2>&1 &")
                    else:
                        log(f"▶ 后台启动: {start_cmd}")
                        await run(f"cd {request.deploy_dir} && nohup {start_cmd} > app.log 2>&1 &")

            # ── 8. 验证部署结果 ───────────────────────────────────
            log("\n▶ 验证部署结果...")
            await asyncio.sleep(4)

            health_cmd = plan.get('health_check') or (
                f"curl -s http://127.0.0.1:{plan.get('expected_port', 8000)}/health 2>/dev/null | grep -q 'ok' && echo 'OK' || "
                f"(ss -tlnp | grep ':{plan.get('expected_port', 8000)}' 2>/dev/null || "
                f"ps aux | grep -E 'python|node|java|gunicorn' | grep -v grep | grep -v agent.py | head -3)"
            )

            health_r = await conn.run(health_cmd, check=False)
            health_out = (health_r.stdout or health_r.stderr or "").strip()

            app_log_r = await conn.run(
                f"tail -20 {request.deploy_dir}/app.log 2>/dev/null || "
                f"journalctl -u {request.service_name or 'app'} -n 10 --no-pager 2>/dev/null || echo ''",
                check=False
            )
            app_log_out = (app_log_r.stdout or "").strip()

            final_prompt = f"""判断以下应用部署是否成功，给出简洁结论：

健康检查结果：{health_out or '无输出'}
应用日志（最后20行）：{app_log_out[-500:] if app_log_out else '无'}

判断标准：
- 有进程在运行 → 成功
- 端口在监听 → 成功  
- 日志有 ModuleNotFoundError/ImportError/Error → 失败
- 日志有 started/running/listening → 成功

如果部署失败，请：
1. 明确指出失败原因
2. 如果是 deploy.sh 问题，提供具体的修改建议
3. 如果是依赖问题，提供安装命令
4. 如果是配置问题，提供修改方案

只回答：✅ 部署成功 或 ❌ 部署失败，然后说明原因，如果失败给出具体的修复建议（包括需要修改的文件路径和代码）。"""

            verdict = await LLM.chat([{"role": "user", "content": final_prompt}], max_tokens=400)
            log(f"\n── 部署验证结果 ──\n{verdict}\n─────────────────")

            if ("❌" in verdict or "失败" in verdict) and "exists" in (deploy_sh.stdout or ""):
                log("▶ 生成 deploy.sh 修改指导...")
                fix_prompt = f"""以下 deploy.sh 执行导致部署失败，请提供详细的修改指导：

目标系统：{info.os_version}
deploy.sh 错误输出：{output[-1000:] if output else '无'}
应用日志错误：{app_log_out[-300:] if app_log_out else '无'}
验证结果：{verdict}

请提供：
1. deploy.sh 中需要修改的具体行数和代码
2. 修改后的完整代码片段
3. 需要额外执行的命令来修复问题
4. 如何验证修复是否成功

以以下格式回答：
━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 deploy.sh 修改指导
━━━━━━━━━━━━━━━━━━━━━━━━━━━

问题定位：
[具体问题描述]

需要修改的位置：
[文件名:行号] 原代码 → 修改后代码

修复代码：
```bash
# 完整的修复代码
```

执行命令：
```bash
# 需要执行的命令
```

验证方法：
[验证修复是否成功的方法]
━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

                try:
                    fix_guide = await LLM.chat([{"role": "user", "content": fix_prompt}], max_tokens=800)
                    log(f"\n{fix_guide}\n")
                except Exception as e:
                    log(f"⚠️  生成修改指导失败: {e}")

            if "❌" in verdict or "失败" in verdict:
                d.status = AppDeployStatus.FAILED
            else:
                d.status = AppDeployStatus.SUCCESS

        finally:
            conn.close()

    except Exception as e:
        log(f"❌ 部署失败: {e}")
        d.status = AppDeployStatus.FAILED
    finally:
        d.completed_at = datetime.now().isoformat()
        _save_app_deploys()
        _append_deploy_log(deploy_id, d.log)
