"""网络检测 API 路由"""
import asyncio
import uuid
import sys, os
import ipaddress
import socket as _socket
from datetime import datetime
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from fastapi import APIRouter, HTTPException
from netcheck.models import CheckTask, CheckRequest, NodeResult
from netcheck.checker import check_node
from netcheck.analyzer import ai_analyze_node, ai_summary

router = APIRouter(prefix="/netcheck", tags=["netcheck"])

# 内存存储检测任务
_tasks: dict = {}

# 并发控制：最多50个并发探测
_semaphore = asyncio.Semaphore(50)

# SSRF 防护：禁止探测内网地址
_BLOCKED_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
]

def _is_safe_target(target: str) -> bool:
    """检查目标是否为安全的公网地址"""
    try:
        host = target.split("/")[0].split(":")[0]
        ip = _socket.gethostbyname(host)
        addr = ipaddress.ip_address(ip)
        return not any(addr in net for net in _BLOCKED_RANGES)
    except Exception:
        return True  # 解析失败时放行，让后续命令自然失败


from pydantic import BaseModel as _BM

class PingRequest(_BM):
    agent_id: str
    target: str

@router.post("/ping")
async def quick_ping(req: PingRequest):
    """快速单次 ping，返回延迟；同时触发后台完整检测任务（首次调用）"""
    if not _is_safe_target(req.target):
        raise HTTPException(400, "目标地址不合法")
    from routers.agents import _ws_call
    from core.state import agents as _agents

    agent = _agents.get(req.agent_id)
    os_type = getattr(agent.os_type, "value", str(agent.os_type)) if agent else "linux"
    is_win = "windows" in os_type.lower()

    if is_win:
        cmd = f"ping -n 1 {req.target}"
    else:
        cmd = f"ping -c 1 -W 3 {req.target} 2>/dev/null"

    try:
        resp = await _ws_call(req.agent_id, {"type": "exec", "command": cmd, "timeout": 10}, timeout=12)
        raw = resp.get("output", "") or ""
        import re
        # 解析延迟
        m = re.search(r'time[=<]([\d.]+)\s*ms', raw, re.IGNORECASE)
        if not m:
            m = re.search(r'([\d.]+)\s*ms', raw)
        latency = round(float(m.group(1))) if m else 0
        loss = latency == 0 or "100%" in raw or "unreachable" in raw.lower()
    except Exception as e:
        return {"latency_ms": 0, "loss": True, "error": str(e)}

    # 首次调用时触发完整检测任务（后台）
    task_id = None
    cache_key = f"{req.agent_id}:{req.target}"
    if cache_key not in _ping_task_cache:
        task_id = f"nc-{uuid.uuid4().hex[:8]}"
        task = CheckTask(
            task_id=task_id, target=req.target, agent_ids=[req.agent_id],
            status="running", created_at=datetime.now().isoformat()
        )
        _tasks[task_id] = task
        _ping_task_cache[cache_key] = task_id
        asyncio.create_task(_run_check(task))

    return {"latency_ms": latency, "loss": loss, "task_id": _ping_task_cache.get(cache_key)}

_ping_task_cache: dict = {}

# ── 浏览器端探针接口 ──────────────────────────────────────────

import time as _time
from fastapi import Request as _Request

# DNS probe token 存储：{token: {created_at, http_ip, probe_ips}}
_dns_probes: dict = {}


async def _get_ip_info(ip: str) -> dict:
    """查询 IP 地理和类型信息"""
    import aiohttp, ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    info = {"ip": ip, "city": "", "country": "", "org": "", "type": "unknown"}
    try:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(f"https://ipinfo.io/{ip}/json",
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    info["city"] = d.get("city", "")
                    info["country"] = d.get("country", "")
                    info["org"] = d.get("org", "")
                    from netcheck.checker import classify_ip
                    info["type"] = classify_ip(d.get("org", "")).value
    except Exception:
        pass
    return info


def _real_ip(request: _Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host


@router.get("/probe/myip")
async def probe_myip(request: _Request):
    """返回请求方的真实 IP 及 IP 类型（供浏览器端检测用）"""
    return await _get_ip_info(_real_ip(request))


@router.get("/probe/dns-token")
async def probe_dns_token(request: _Request):
    """生成一次性 DNS probe token"""
    tok = uuid.uuid4().hex
    _dns_probes[tok] = {
        "created_at": _time.time(),
        "http_ip": _real_ip(request),
        "probe_ips": [],
    }
    return {"token": tok, "probe_domain": None}


@router.get("/probe/dns-probe/{token}")
async def probe_dns_record(token: str, request: _Request):
    """浏览器访问此 URL 时，记录来源 IP（模拟 DNS 查询来源）"""
    if token in _dns_probes:
        _dns_probes[token]["probe_ips"].append(_real_ip(request))
    from fastapi.responses import Response
    gif = b'GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;'
    return Response(content=gif, media_type="image/gif")


@router.get("/probe/dns-result")
async def probe_dns_result(token: str, request: _Request):
    """查询 DNS probe 结果"""
    if token not in _dns_probes:
        raise HTTPException(404, "token 不存在或已过期")

    probe = _dns_probes[token]
    http_ip = probe["http_ip"]
    probe_ips = probe.get("probe_ips", [])

    # Google DoH 查询作为参考
    google_ips = []
    try:
        import aiohttp, ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get("https://dns.google/resolve?name=www.tiktok.com&type=A",
                             headers={"Accept": "application/dns-json"},
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json(content_type=None)
                    google_ips = [a["data"] for a in d.get("Answer", []) if a.get("type") == 1]
    except Exception:
        pass

    leaked = bool(probe_ips and any(ip != http_ip for ip in probe_ips))

    # 清理过期 token（5分钟）
    now = _time.time()
    for k in [k for k, v in _dns_probes.items() if now - v["created_at"] > 300]:
        del _dns_probes[k]

    return {
        "leaked": leaked,
        "http_ip": http_ip,
        "dns_resolver_ips": probe_ips or google_ips[:3],
        "google_doh_ips": google_ips[:3],
        "method": "http_probe" if probe_ips else "doh_fallback",
    }


# ── IP 反向侦察：用我们的 agent 节点 traceroute 目标 IP ──────

from pydantic import BaseModel as _BM2

class ScanRequest(_BM2):
    target_ip: str          # 要探测的 IP（用户的出口 IP 或手动输入）
    agent_ids: List[str] = []  # 为空时自动选取在线节点

_scan_tasks: dict = {}

@router.post("/probe/scan")
async def probe_scan(req: ScanRequest, request: _Request):
    """
    用我们的 agent 节点 traceroute 目标 IP，分析路由画像。
    用户进入网页时自动触发（target_ip = 用户出口 IP）。
    也支持用户手动输入任意公网 IP。
    """
    # SSRF 防护
    if not _is_safe_target(req.target_ip):
        raise HTTPException(400, "目标 IP 不合法")

    from core.state import agents as _agents

    # 自动选取在线 agent（最多3个，优先选不同地区）
    agent_ids = req.agent_ids
    if not agent_ids:
        online = [a for a in _agents.values() if a.status == "online"]
        agent_ids = [a.agent_id for a in online[:3]]

    if not agent_ids:
        raise HTTPException(503, "暂无可用节点，请稍后重试")

    task_id = f"scan-{uuid.uuid4().hex[:8]}"
    _scan_tasks[task_id] = {
        "task_id": task_id,
        "target_ip": req.target_ip,
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "results": [],
        "ip_profile": {},
        "completed_at": "",
    }
    asyncio.create_task(_run_scan(task_id, req.target_ip, agent_ids))
    return {"task_id": task_id, "status": "running", "agent_count": len(agent_ids)}


@router.get("/probe/scan/{task_id}")
async def get_scan(task_id: str):
    if task_id not in _scan_tasks:
        raise HTTPException(404, "任务不存在")
    return _scan_tasks[task_id]


async def _run_scan(task_id: str, target_ip: str, agent_ids: List[str]):
    """后台执行：多节点 traceroute 目标 IP，分析路由画像"""
    from core.state import agents as _agents
    from routers.agents import _ws_call
    from netcheck.checker import enrich_hops, parse_traceroute, is_private_ip

    task = _scan_tasks[task_id]

    # 1. 查询目标 IP 信息
    ip_profile = await _get_ip_info(target_ip)
    task["ip_profile"] = ip_profile

    # 2. 多节点并发 traceroute
    is_ipv6 = ":" in target_ip  # IPv6 地址包含冒号

    async def trace_one(agent_id: str) -> dict:
        agent = _agents.get(agent_id)
        os_type = getattr(agent.os_type, "value", str(agent.os_type)) if agent else "linux"
        name = (agent.name if agent else agent_id) or agent_id
        is_win = "windows" in os_type.lower()
        is_android = "android" in os_type.lower()

        # 外网连通性预检
        try:
            chk = f"ping -n 1 -w 3000 {target_ip}" if is_win else f"ping -c 1 -W 3 {target_ip} 2>/dev/null"
            chk_resp = await _ws_call(agent_id, {"type": "exec", "command": chk, "timeout": 8}, timeout=10)
            chk_out = chk_resp.get("output", "") or ""
            if "100% packet loss" in chk_out or "0 received" in chk_out or \
               (is_win and "请求超时" in chk_out and "TTL" not in chk_out):
                return {"agent_id": agent_id, "name": name, "os_type": os_type,
                        "status": "failed", "error": f"无法访问外网（ping {target_ip} 失败）",
                        "hops": [], "total_hops": 0, "valid_hops": 0, "timeout_hops": 0,
                        "private_hops": 0, "last3": [], "all_hops": [], "last_latency": 0}
        except Exception:
            pass

        if is_ipv6:
            # IPv6：traceroute6 或 ping6，取延迟为主
            if is_win:
                cmd = f"ping -6 -n 3 {target_ip}"
            elif is_android:
                cmd = f"ping6 -c 3 {target_ip} 2>/dev/null || ping -c 3 {target_ip}"
            else:
                cmd = f"traceroute6 -n -m 15 -w 2 {target_ip} 2>/dev/null || ping6 -c 3 {target_ip} 2>/dev/null || ping -c 3 {target_ip}"
        elif is_win:
            cmd = f"tracert -d -h 20 {target_ip}"
        elif is_android:
            cmd = f"ping -c 3 {target_ip}"
        else:
            cmd = f"traceroute -n -m 20 -w 2 {target_ip} 2>/dev/null || tracepath -n {target_ip} 2>/dev/null"

        try:
            resp = await _ws_call(agent_id, {"type": "exec", "command": cmd, "timeout": 60}, timeout=75)
            raw = resp.get("output", "") or ""
        except Exception as e:
            return {"agent_id": agent_id, "name": name, "os_type": os_type,
                    "status": "failed", "error": str(e), "hops": []}

        hops_raw = parse_traceroute(raw)
        hops_enriched = await enrich_hops(hops_raw)

        valid = [h for h in hops_enriched if h["ip"] != "*" and not is_private_ip(h["ip"])]
        private_count = sum(1 for h in hops_enriched if h["ip"] != "*" and is_private_ip(h["ip"]))
        star_count = sum(1 for h in hops_enriched if h["ip"] == "*")

        # 最后3跳（最接近目标的节点）
        last3 = valid[-3:] if len(valid) >= 3 else valid

        # 计算到目标的延迟（最后一跳）
        last_latency = 0
        for h in reversed(hops_enriched):
            if h.get("avg", 0) > 0:
                last_latency = h["avg"]
                break

        return {
            "agent_id": agent_id,
            "name": name,
            "os_type": os_type,
            "status": "success",
            "total_hops": len(hops_enriched),
            "valid_hops": len(valid),
            "private_hops": private_count,
            "timeout_hops": star_count,
            "last3": last3,
            "all_hops": hops_enriched,
            "last_latency": last_latency,
        }

    async def trace_safe(agent_id: str) -> dict:
        async with _semaphore:
            return await trace_one(agent_id)

    results = await asyncio.gather(*[trace_safe(aid) for aid in agent_ids])
    task["results"] = list(results)

    # 3. 综合分析：判断 IP 画像
    task["analysis"] = _analyze_ip_profile(ip_profile, list(results))
    task["status"] = "success"
    task["completed_at"] = datetime.now().isoformat()

    # 4. 存入数据库
    try:
        from netcheck.trace_db import save_traceroute
        for r in results:
            if r.get("status") == "success" and r.get("all_hops"):
                save_traceroute(
                    task_id=task_id,
                    target=target_ip,
                    target_type="ip",
                    source="probe",
                    agent_id=r.get("agent_id", ""),
                    agent_name=r.get("name", ""),
                    os_type=r.get("os_type", ""),
                    hops=r.get("all_hops", []),
                    last_latency_ms=r.get("last_latency", 0),
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"save traceroute failed: {e}")


def _analyze_ip_profile(ip_profile: dict, results: list) -> dict:
    """
    综合 IP 信息和 traceroute 结果，给出 IP 画像判断。
    不用 AI，纯规则，快速返回。
    """
    ip_type = ip_profile.get("type", "unknown")
    org = ip_profile.get("org", "")
    country = ip_profile.get("country", "")

    flags = []
    score = 100

    # IP 类型
    if ip_type == "datacenter":
        flags.append("🏢 机房IP — TikTok 账号权重低")
        score -= 40
    elif ip_type == "proxy":
        flags.append("🔀 代理/VPN IP — 高风控风险")
        score -= 50
    elif ip_type == "residential":
        flags.append("🏠 住宅IP — TikTok 友好")
        score += 0

    # 路由特征分析
    all_hops = []
    for r in results:
        if r.get("status") == "success":
            all_hops.extend(r.get("all_hops", []))

    # 隧道代理检测已移除：从外部 traceroute 无法判断目标 IP 背后是否有隧道
    # 路径中的内网跳是探测节点自身的网络结构，不代表目标 IP 的特征

    # 延迟分析
    latencies = [r.get("last_latency", 0) for r in results if r.get("last_latency", 0) > 0]
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else 0
    if avg_latency > 500:
        flags.append(f"🐌 延迟极高（{avg_latency}ms）— 代理服务器可能过载")
        score -= 20
    elif avg_latency > 200:
        flags.append(f"⚠️ 延迟偏高（{avg_latency}ms）")
        score -= 10

    # 路径长度
    hop_counts = [r.get("total_hops", 0) for r in results if r.get("status") == "success"]
    avg_hops = round(sum(hop_counts) / len(hop_counts)) if hop_counts else 0
    if avg_hops > 15:
        flags.append(f"📏 路径过长（平均{avg_hops}跳）— 绕路严重")
        score -= 15

    score = max(0, min(100, score))

    verdict = "适合 TikTok 直播" if score >= 70 else \
              "存在风险，建议优化" if score >= 40 else \
              "高风险，不建议用于 TikTok"

    return {
        "score": score,
        "verdict": verdict,
        "flags": flags,
        "avg_latency": avg_latency,
        "avg_hops": avg_hops,
        "ip_type": ip_type,
        "org": org,
        "country": country,
    }


@router.post("/tasks")
async def create_check(req: CheckRequest):
    """创建网络检测任务"""
    if not _is_safe_target(req.target):
        raise HTTPException(400, "目标地址不合法（禁止探测内网地址）")
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
        os_type = getattr(agent.os_type, "value", str(agent.os_type)) if agent else "linux"
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
    if not _is_safe_target(req.target):
        raise HTTPException(400, "目标地址不合法（禁止探测内网地址）")
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
        os_type = getattr(agent.os_type, "value", str(agent.os_type)) if agent else "linux"
        name = (agent.name if agent else agent_id) or agent_id
        is_win = "windows" in os_type.lower()
        is_android = "android" in os_type.lower()

        # 外网连通性预检：先 ping 目标，失败直接跳过
        try:
            if is_win:
                check_cmd = f"ping -n 1 -w 3000 {target}"
            else:
                check_cmd = f"ping -c 1 -W 3 {target} 2>/dev/null"
            check_resp = await _ws_call(agent_id, {"type": "exec", "command": check_cmd, "timeout": 8}, timeout=10)
            check_out = check_resp.get("output", "") or ""
            # 判断是否可达
            if "100% packet loss" in check_out or "100% 丢失" in check_out or \
               ("transmitted" in check_out and "0 received" in check_out) or \
               (is_win and "请求超时" in check_out and "TTL" not in check_out):
                return {"agent_id": agent_id, "name": name, "os_type": os_type,
                        "status": "failed", "error": f"无法访问外网（ping {target} 失败）", "hops": [],
                        "total_hops": 0, "valid_hops": 0, "timeout_hops": 0, "last5": [], "all_hops": []}
        except Exception:
            pass  # 预检失败不阻止，继续尝试 traceroute

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

    async def probe_one_safe(agent_id: str) -> dict:
        async with _semaphore:
            return await probe_one(agent_id)

    results = await asyncio.gather(*[probe_one_safe(aid) for aid in agent_ids])
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

    # 存入数据库
    try:
        from netcheck.trace_db import save_traceroute
        for r in results:
            if r.get("status") == "success" and r.get("all_hops"):
                save_traceroute(
                    task_id=task["task_id"],
                    target=target,
                    target_type="domain",
                    source="recon",
                    agent_id=r.get("agent_id", ""),
                    agent_name=r.get("name", ""),
                    os_type=r.get("os_type", ""),
                    hops=r.get("all_hops", []),
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"save recon traceroute failed: {e}")
