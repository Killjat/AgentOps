"""AgentOps 控制端服务器"""
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 自动加载 .env
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

import sys
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "swarm"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from core.storage import _load_persistent_data, _save_agents, _save_tasks, _save_app_deploys
from routers import auth, servers, agents, tasks, deploy
from routers.agents import ws_agent_endpoint
from routers import sync as sync_router
import swarm.router as swarm_router
import netcheck.router as netcheck_router

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("[lifespan] 开始加载持久化数据...")
    try:
        from core.db import init_db
        init_db()
        _load_persistent_data()
        logger.info("[lifespan] 数据加载完成")

        # 启动自动化分析调度器
        try:
            from netcheck.scheduler import start_scheduler
            await start_scheduler()
            logger.info("[lifespan] 分析调度器已启动")
        except Exception as e:
            logger.warning(f"[lifespan] 调度器启动失败: {e}")
    except Exception as e:
        logger.error(f"[lifespan] 数据加载失败: {e}")
        import traceback
        traceback.print_exc()

    # 启动双向同步
    from sync import start_sync
    start_sync()

    yield

    logger.info("[lifespan] 开始保存数据...")
    try:
        _save_agents()
        _save_tasks()
        _save_app_deploys()
        logger.info("[持久化] 数据已保存")
    except Exception as e:
        logger.error(f"[lifespan] 数据保存失败: {e}")
        import traceback
        traceback.print_exc()


app = FastAPI(title="CyberAgentOps", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── 注册路由 ─────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(agents.router)
app.include_router(tasks.router)
app.include_router(deploy.router)
app.include_router(swarm_router.router)
app.include_router(sync_router.router)
app.include_router(netcheck_router.router)

# ── WebSocket ────────────────────────────────────────────────────
app.add_api_websocket_route("/ws/agent/{agent_id}", ws_agent_endpoint)


# ── 健康检查 ─────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "CyberAgentOps"}


@app.get("/download/agent/{platform}")
async def download_agent(platform: str):
    """下载对应平台的 Agent 二进制"""
    from fastapi.responses import FileResponse
    import os
    dist_dir = Path(__file__).parent.parent / "agent" / "dist"
    files = {
        "linux":   dist_dir / "cyberagent-linux",
        "macos":   dist_dir / "cyberagent-macos",
        "windows": dist_dir / "cyberagent-windows.exe",
        "android": Path(__file__).parent.parent / "agent" / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "cyberagent.apk",
    }
    if platform not in files:
        raise HTTPException(status_code=404, detail="不支持的平台")
    f = files[platform]
    if not f.exists():
        raise HTTPException(status_code=404, detail=f"{platform} 版本尚未编译，请先运行 build_agent.sh")
    return FileResponse(str(f), filename=f.name, media_type="application/octet-stream")


# ── 静态文件 / Web 界面 ──────────────────────────────────────────
if WEB_DIR.exists():
    @app.get("/", include_in_schema=False)
    async def serve_index():
        content = (WEB_DIR / "index.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/admin", include_in_schema=False)
    async def serve_admin():
        content = (WEB_DIR / "index.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/swarm-ui", include_in_schema=False)
    async def serve_swarm():
        content = (WEB_DIR / "swarm.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/netcheck-ui", include_in_schema=False)
    async def serve_netcheck():
        content = (WEB_DIR / "netcheck.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/probe", include_in_schema=False)
    async def serve_probe():
        content = (WEB_DIR / "probe.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/batch-scan", include_in_schema=False)
    async def serve_batch_scan():
        content = (WEB_DIR / "batch_scan.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/insights", include_in_schema=False)
    async def serve_insights():
        content = (WEB_DIR / "insights.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/portscan", include_in_schema=False)
    async def serve_portscan():
        content = (WEB_DIR / "portscan.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    @app.get("/ecom-intel", include_in_schema=False)
    async def serve_ecom_intel():
        content = (WEB_DIR / "ecom_intel.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    # ── 一键安装脚本 ──────────────────────────────────────────
    SERVER_URL_PUBLIC = os.getenv("SERVER_URL", "https://47.111.28.162:8443")
    AGENT_BASE_URL = "https://github.com/Killjat/agentops/releases/latest/download"

    @app.get("/agent/package/{platform}")
    async def agent_package(platform: str, token: str = "", arch: str = ""):
        """打包下载：二进制 + agent.conf，用户解压双击即可"""
        import secrets as _sec, zipfile, io
        from fastapi.responses import StreamingResponse
        from pathlib import Path as _Path
        if not token:
            token = _sec.token_hex(8)

        # mac 根据 arch 参数选择版本
        platform_map = {
            "mac":     ("cyberagent-mac",         "cyberagent-mac"),
            "mac-intel": ("cyberagent-mac-intel",  "cyberagent-mac"),
            "linux":   ("cyberagent-linux",        "cyberagent-linux"),
            "windows": ("cyberagent-windows.exe",  "cyberagent.exe"),
        }
        # mac 自动选架构
        if platform == "mac" and arch == "x86_64":
            platform = "mac-intel"

        if platform not in platform_map:
            return {"error": "unsupported platform"}

        bin_name, local_name = platform_map[platform]

        # 优先从服务器缓存目录读取，其次从 agent/dist 读取
        search_paths = [
            _Path("/opt/cyberagentops/agent_binaries") / bin_name,
            _Path(__file__).parent.parent / "agent" / "dist" / bin_name,
        ]
        binary_data = None
        for p in search_paths:
            if p.exists():
                binary_data = p.read_bytes()
                break

        if not binary_data:
            raise HTTPException(404, f"Agent binary not found. Please build first.")

        # 生成 agent.conf
        conf = f"SERVER_URL={SERVER_URL_PUBLIC}\nAGENT_TOKEN={token}\n"

        # 打包 zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("agent.conf", conf)
            info = zipfile.ZipInfo(local_name)
            info.external_attr = 0o755 << 16
            zf.writestr(info, binary_data)
            if platform != "windows":
                readme = f"1. 解压此 zip\n2. 双击 {local_name} 运行（Mac 首次右键→打开）\n3. 返回浏览器等待连接\n"
                zf.writestr("README.txt", readme)

        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=cyberstroll-agent-{platform}.zip"}
        )

    static_dir = WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
