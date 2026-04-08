"""
节点双向同步引擎
- 每个节点暴露 /sync/pull 和 /sync/push
- 定时任务每30秒与对端同步一次
- 冲突策略：last_write_wins（时间戳更新的胜出）
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp
import ssl

logger = logging.getLogger(__name__)

# 对端节点地址，从环境变量读取
# 例如：PEER_URL=https://47.111.28.162:8443
PEER_URL: Optional[str] = None
SYNC_INTERVAL = 30  # 秒
_sync_task: Optional[asyncio.Task] = None


def get_peer_url() -> Optional[str]:
    return os.getenv("PEER_URL", "").strip() or None


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def pull_from_peer(peer_url: str) -> dict:
    """从对端拉取数据"""
    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                f"{peer_url}/sync/pull",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.warning(f"[sync] 从 {peer_url} 拉取失败: {e}")
    return {}


async def push_to_peer(peer_url: str, data: dict) -> bool:
    """推送本地数据到对端"""
    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                f"{peer_url}/sync/push",
                json=data,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                return resp.status == 200
    except Exception as e:
        logger.warning(f"[sync] 推送到 {peer_url} 失败: {e}")
    return False


def get_local_snapshot() -> dict:
    """获取本地数据快照（用于 /sync/pull 响应）"""
    from core.state import agents, tasks
    from core.storage import _load_servers_yaml

    agents_data = {}
    for aid, a in agents.items():
        try:
            agents_data[aid] = a.model_dump(mode='json', exclude_none=True)
        except Exception:
            pass

    tasks_data = {}
    for tid, t in tasks.items():
        try:
            tasks_data[tid] = t.model_dump(mode='json', exclude_none=True)
        except Exception:
            pass

    # swarm 任务从 DB 读
    swarm_data = {}
    try:
        from core.db import load_swarm_tasks
        for t in load_swarm_tasks():
            swarm_data[t["swarm_task_id"]] = t
    except Exception:
        pass

    return {
        "timestamp": datetime.now().isoformat(),
        "agents": agents_data,
        "tasks": tasks_data,
        "swarm_tasks": swarm_data,
    }


def merge_snapshot(remote: dict):
    """将对端数据合并到本地（last_write_wins）"""
    from core.state import agents, tasks
    from core.storage import _save_agents, _save_tasks
    from models import AgentInfo, TaskResult, AgentStatus, ConnectionType

    changed_agents = False
    changed_tasks = False

    # 合并 agents
    for aid, remote_data in remote.get("agents", {}).items():
        local = agents.get(aid)
        remote_ts = remote_data.get("last_seen", "")
        local_ts = local.last_seen if local else ""

        if remote_ts > local_ts:
            try:
                agent = AgentInfo(**remote_data)
                # 远端 agent 在本地不可能有 WebSocket 连接，标记为 offline
                if agent.connection_type == ConnectionType.AGENT_PUSH:
                    agent.status = AgentStatus.OFFLINE
                agents[aid] = agent
                changed_agents = True
            except Exception as e:
                logger.warning(f"[sync] 合并 agent {aid} 失败: {e}")

    # 合并 tasks
    for tid, remote_data in remote.get("tasks", {}).items():
        local = tasks.get(tid)
        remote_ts = remote_data.get("created_at", "")
        local_ts = local.created_at if local else ""

        if not local or remote_ts > local_ts:
            try:
                tasks[tid] = TaskResult(**remote_data)
                changed_tasks = True
            except Exception as e:
                logger.warning(f"[sync] 合并 task {tid} 失败: {e}")

    # 合并 swarm tasks 到 DB
    try:
        from core.db import save_swarm_task, get_swarm_task
        from swarm.swarm_models import SwarmTask
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'swarm'))

        for sid, remote_data in remote.get("swarm_tasks", {}).items():
            local = get_swarm_task(sid)
            remote_ts = remote_data.get("created_at", "")
            local_ts = local.get("created_at", "") if local else ""
            if not local or remote_ts > local_ts:
                try:
                    task = SwarmTask(**remote_data)
                    save_swarm_task(task)
                except Exception as e:
                    logger.warning(f"[sync] 合并 swarm_task {sid} 失败: {e}")
    except Exception as e:
        logger.warning(f"[sync] swarm 任务合并失败: {e}")

    if changed_agents:
        try:
            _save_agents()
        except Exception:
            pass
    if changed_tasks:
        try:
            _save_tasks()
        except Exception:
            pass

    logger.info(f"[sync] 合并完成: agents={len(remote.get('agents',{}))}, tasks={len(remote.get('tasks',{}))}, swarm={len(remote.get('swarm_tasks',{}))}")


async def sync_once():
    """执行一次双向同步"""
    peer_url = get_peer_url()
    if not peer_url:
        return

    logger.debug(f"[sync] 开始与 {peer_url} 同步...")

    # 1. 拉取对端数据并合并
    remote = await pull_from_peer(peer_url)
    if remote:
        merge_snapshot(remote)

    # 2. 推送本地数据到对端
    local = get_local_snapshot()
    await push_to_peer(peer_url, local)


async def sync_loop():
    """后台同步循环"""
    await asyncio.sleep(10)  # 启动后等10秒再开始
    while True:
        try:
            await sync_once()
        except Exception as e:
            logger.error(f"[sync] 同步异常: {e}")
        await asyncio.sleep(SYNC_INTERVAL)


def start_sync():
    """启动后台同步任务"""
    global _sync_task
    peer_url = get_peer_url()
    if not peer_url:
        logger.info("[sync] 未配置 PEER_URL，跳过同步")
        return
    logger.info(f"[sync] 启动双向同步，对端: {peer_url}，间隔: {SYNC_INTERVAL}s")
    _sync_task = asyncio.create_task(sync_loop())
