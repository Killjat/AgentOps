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
