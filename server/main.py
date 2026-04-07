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
import swarm.router as swarm_router

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("[lifespan] 开始加载持久化数据...")
    try:
        _load_persistent_data()
        logger.info("[lifespan] 数据加载完成")
    except Exception as e:
        logger.error(f"[lifespan] 数据加载失败: {e}")
        import traceback
        traceback.print_exc()

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

    @app.get("/swarm-ui", include_in_schema=False)
    async def serve_swarm():
        content = (WEB_DIR / "swarm.html").read_text()
        return HTMLResponse(content=content, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache"
        })

    static_dir = WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
