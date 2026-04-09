"""网络检测 API 路由"""
import asyncio
import uuid
import sys, os
from datetime import datetime
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from fastapi import APIRouter
from netcheck.models import CheckTask, CheckRequest, NodeResult
from netcheck.checker import check_node
from netcheck.analyzer import ai_analyze_node, ai_summary

router = APIRouter(prefix="/netcheck", tags=["netcheck"])

# 内存存储检测任务
_tasks: dict = {}


@router.post("/tasks")
async def create_check(req: CheckRequest):
    """创建网络检测任务"""
    task_id = f"nc-{uuid.uuid4().hex[:8]}"
    task = CheckTask(
        task_id=task_id,
        target=req.target,
        agent_ids=req.agent_ids,
        status="running",
        created_at=datetime.now().isoformat(),
    )
    _tasks[task_id] = task
    asyncio.create_task(_run_check(task))
    return {"task_id": task_id, "status": "running"}


@router.get("/tasks")
async def list_tasks():
    return list(_tasks.values())


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in _tasks:
        from fastapi import HTTPException
        raise HTTPException(404, "任务不存在")
    return _tasks[task_id]


async def _run_check(task: CheckTask):
    """后台执行检测"""
    from core.state import agents

    async def check_one(agent_id: str) -> NodeResult:
        agent = agents.get(agent_id)
        os_type = str(agent.os_type) if agent else "linux"
        name = agent.name if agent else agent_id
        result = await check_node(agent_id, task.target, os_type)
        result.agent_name = name
        # AI 分析单节点
        result = await ai_analyze_node(result, task.target)
        return result

    # 并行检测所有节点
    results = await asyncio.gather(*[check_one(aid) for aid in task.agent_ids])
    task.results = list(results)

    # AI 生成整体报告
    task.summary = await ai_summary(task)
    task.status = "success" if any(r.status == "success" for r in results) else "failed"
    task.completed_at = datetime.now().isoformat()

# ── 目标侦察 ──────────────────────────────────────────────────

from pydantic import BaseModel

class ReconRequest(BaseModel):
    target: str
    agent_ids: List[str]  # 用哪些节点探测

_recon_tasks: dict = {}


@router.post("/recon")
async def create_recon(req: ReconRequest):
    """目标侦察：从多节点并发 traceroute，分析目标服务器网络画像"""
    task_id = f"recon-{uuid.uuid4().hex[:8]}"
    task = {
        "task_id": task_id,
        "target": req.target,
        "agent_ids": req.agent_ids,
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "results": [],
        "summary": "",
        "completed_at": "",
    }
    _recon_tasks[task_id] = task
    asyncio.create_task(_run_recon(task_id, req.target, req.agent_ids))
    return {"task_id": task_id, "status": "running"}


@router.get("/recon/{task_id}")
async def get_recon(task_id: str):
    if task_id not in _recon_tasks:
        from fastapi import HTTPException
        raise HTTPException(404, "任务不存在")
    return _recon_tasks[task_id]


async def _run_recon(task_id: str, target: str, agent_ids: List[str]):
    from core.state import agents
    from routers.agents import _ws_call
    from netcheck.checker import enrich_hops, parse_traceroute, classify_ip, is_private_ip

    task = _recon_tasks[task_id]

    async def probe_one(agent_id: str) -> dict:
        agent = agents.get(agent_id)
        os_type = str(agent.os_type) if agent else "linux"
        name = (agent.name if agent else agent_id) or agent_id
        is_win = "windows" in os_type.lower()
        is_android = "android" in os_type.lower()

        if is_win:
            cmd = f"tracert -d -h 20 {target}"
        elif is_android:
            cmd = f"ping -c 3 {target}"
        else:
            cmd = f"traceroute -n -m 20 -w 2 {target} 2>/dev/null || tracepath -n -m 20 {target} 2>/dev/null"

        try:
            resp = await _ws_call(agent_id, {"type": "exec", "command": cmd, "timeout": 60}, timeout=70)
            raw = resp.get("output", "") or ""
        except Exception as e:
            return {"agent_id": agent_id, "name": name, "status": "failed", "error": str(e), "hops": []}

        hops_raw = parse_traceroute(raw)
        # 服务器端对跳点做地理标注
        hops_enriched = await enrich_hops(hops_raw)

        # 分析最后5个有效公网跳
        valid = [h for h in hops_enriched if h["ip"] != "*" and not is_private_ip(h["ip"])]
        last5 = valid[-5:] if len(valid) >= 5 else valid

        # 统计特征
        star_count = sum(1 for h in hops_enriched if h["ip"] == "*")
        private_count = sum(1 for h in hops_enriched if h["ip"] != "*" and is_private_ip(h["ip"]))

        return {
            "agent_id": agent_id,
            "name": name,
            "os_type": os_type,
            "status": "success",
            "total_hops": len(hops_enriched),
            "valid_hops": len(valid),
            "timeout_hops": star_count,
            "private_hops": private_count,
            "last5": last5,
            "all_hops": hops_enriched,
        }

    results = await asyncio.gather(*[probe_one(aid) for aid in agent_ids])
    task["results"] = list(results)

    # AI 汇总分析
    try:
        from llm import chat
        nodes_desc = []
        for r in results:
            if r["status"] == "success" and r.get("last5"):
                hops_str = " → ".join(
                    f"{h['ip']}({h.get('city','')},{h.get('country','')})" for h in r["last5"]
                )
                nodes_desc.append(f"- {r['name']}({r['os_type']}): 最后5跳 {hops_str}")

        prompt = f"""你是网络分析专家。以下是从多个节点对 {target} 进行 traceroute 的结果。

{chr(10).join(nodes_desc)}

请分析：
1. 目标服务器托管在哪个城市/机房/运营商？
2. 各节点到达目标的路径有何差异？
3. 目标是否有 CDN 或多接入点？
4. 哪个节点访问目标延迟最低？为什么？

用简洁中文，重点突出关键发现。"""

        task["summary"] = await chat([{"role": "user", "content": prompt}], max_tokens=500)
    except Exception as e:
        task["summary"] = f"AI 分析失败: {e}"

    task["status"] = "success"
    task["completed_at"] = datetime.now().isoformat()
