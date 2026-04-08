"""同步 API 路由"""
from fastapi import APIRouter, HTTPException
from sync import get_local_snapshot, merge_snapshot

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/pull")
async def sync_pull():
    """对端调用此接口拉取本节点数据"""
    return get_local_snapshot()


@router.post("/push")
async def sync_push(data: dict):
    """接收对端推送的数据并合并"""
    try:
        merge_snapshot(data)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxy")
async def sync_proxy(req: dict):
    """代理执行：对端 agent 不在本地时，转发命令到本节点执行"""
    agent_id = req.get("agent_id")
    msg = req.get("msg", {})
    timeout = req.get("timeout", 60)

    if not agent_id or not msg:
        raise HTTPException(status_code=400, detail="缺少 agent_id 或 msg")

    from core.state import _ws_connections
    if agent_id not in _ws_connections:
        raise HTTPException(status_code=503, detail=f"Agent {agent_id} 不在本节点")

    from routers.agents import _ws_call
    return await _ws_call(agent_id, msg, timeout=timeout)


@router.get("/status")
async def sync_status():
    """查看同步状态"""
    import os
    from sync import get_peer_url, SYNC_INTERVAL
    peer = get_peer_url()
    return {
        "peer_url": peer or "未配置",
        "sync_interval": SYNC_INTERVAL,
        "enabled": bool(peer),
    }
