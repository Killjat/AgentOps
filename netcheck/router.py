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
    mode: str = "direct"   # direct=裸连  proxy=走系统代理

@router.post("/ping")
async def quick_ping(req: PingRequest):
    """快速单次延迟检测，优先用 HTTP，fallback 到 ping"""
    if not _is_safe_target(req.target):
        raise HTTPException(400, "目标地址不合法")
    from routers.agents import _ws_call
    from core.state import agents as _agents

    agent = _agents.get(req.agent_id)
    os_type = getattr(agent.os_type, "value", str(agent.os_type)) if agent else "linux"
    is_win = "windows" in os_type.lower()

    # 优先用 HTTP 测延迟（更准确，不受 ICMP 屏蔽影响）
    target = req.target
    mode = req.mode  # direct / proxy
    if not target.startswith("http"):
        http_target = f"https://{target}"
    else:
        http_target = target

    if mode == "proxy":
        # 走系统代理：先探测系统代理端口，再用 curl -x 走代理
        if is_win:
            # Windows：读注册表代理
            detect_cmd = "powershell -Command \"(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings').ProxyServer\""
        else:
            # Mac/Linux：读系统代理（Clash 默认 7890，V2Ray 默认 10809）
            detect_cmd = "networksetup -getwebproxy Wi-Fi 2>/dev/null || echo 'port:7890'"

        try:
            proxy_resp = await _ws_call(req.agent_id, {"type": "exec", "command": detect_cmd, "timeout": 5}, timeout=7)
            proxy_raw = proxy_resp.get("output", "") or ""
            # 提取代理端口
            import re as _re
            port_m = _re.search(r'(?:Port|port|:)\s*(\d{4,5})', proxy_raw)
            proxy_port = port_m.group(1) if port_m else "7890"
            proxy_addr = f"http://127.0.0.1:{proxy_port}"
        except Exception:
            proxy_addr = "http://127.0.0.1:7890"

        if is_win:
            cmd = f"powershell -Command \"$s=Get-Date; try{{Invoke-WebRequest -Uri '{http_target}' -Proxy '{proxy_addr}' -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null; [int](New-TimeSpan -Start $s -End (Get-Date)).TotalMilliseconds}} catch{{0}}\""
        else:
            cmd = f"curl -o /dev/null -s -w '%{{time_total}}' --max-time 5 --connect-timeout 3 -x {proxy_addr} {http_target} 2>/dev/null"
    else:
        # 裸连：强制不走代理
        if is_win:
            cmd = f"powershell -Command \"$s=Get-Date; try{{Invoke-WebRequest -Uri '{http_target}' -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null; [int](New-TimeSpan -Start $s -End (Get-Date)).TotalMilliseconds}} catch{{0}}\""
        else:
            cmd = f"curl -o /dev/null -s -w '%{{time_total}}' --max-time 5 --connect-timeout 3 --noproxy '*' {http_target} 2>/dev/null"

    try:
        resp = await _ws_call(req.agent_id, {"type": "exec", "command": cmd, "timeout": 8}, timeout=10)
        raw = resp.get("output", "") or ""
        raw = raw.strip().strip("'\"")

        latency = 0
        if is_win:
            try:
                latency = int(raw)
            except Exception:
                latency = 0
        else:
            # curl 返回秒数如 0.234，转成毫秒
            try:
                latency = round(float(raw) * 1000)
            except Exception:
                latency = 0

        # HTTP 失败时 fallback 到 ping
        if latency == 0:
            if is_win:
                ping_cmd = f"ping -n 1 {target}"
            else:
                ping_cmd = f"ping -c 1 -W 3 {target} 2>/dev/null"
            ping_resp = await _ws_call(req.agent_id, {"type": "exec", "command": ping_cmd, "timeout": 6}, timeout=8)
            ping_raw = ping_resp.get("output", "") or ""
            m = re.search(r'time[=<]([\d.]+)\s*ms', ping_raw, re.IGNORECASE)
            if not m:
                m = re.search(r'([\d.]+)\s*ms', ping_raw)
            if m:
                latency = round(float(m.group(1)))

        loss = "100%" in raw or "unreachable" in raw.lower() or "0 received" in raw

    except Exception as e:
        return {"latency_ms": 0, "loss": False, "error": str(e)}

    # 首次调用时触发完整检测任务（后台）
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

    return {"latency_ms": latency, "loss": loss, "mode": mode, "task_id": _ping_task_cache.get(cache_key)}

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
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    return request.client.host


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
    import re as _re

    # 如果输入的是域名，先解析成 IP
    resolved_ip = target_ip
    if not _re.match(r'^\d+\.\d+\.\d+\.\d+$', target_ip) and ':' not in target_ip:
        try:
            import socket as _sock
            resolved_ip = _sock.gethostbyname(target_ip)
            # 更新 task 里的 target，但保留原始域名显示
            task = _scan_tasks.get(task_id, {})
            task["original_target"] = target_ip
            task["resolved_ip"] = resolved_ip
        except Exception:
            pass  # 解析失败就用原始值
    from routers.agents import _ws_call
    from netcheck.checker import enrich_hops, parse_traceroute, is_private_ip

    task = _scan_tasks[task_id]

    # 1. 查询目标 IP 信息（用解析后的 IP）
    ip_profile = await _get_ip_info(resolved_ip)
    if target_ip != resolved_ip:
        ip_profile["domain"] = target_ip  # 保留原始域名
        ip_profile["ip"] = resolved_ip
    task["ip_profile"] = ip_profile

    # 2. 多节点并发 traceroute（用解析后的 IP）
    is_ipv6 = ":" in resolved_ip

    # 判断目标是否为境内 IP（中国大陆）
    target_is_cn = ip_profile.get("country", "") in ("CN",) and \
                   ip_profile.get("city", "") not in ("Hong Kong", "Macau", "Taiwan")

    async def trace_one(agent_id: str) -> dict:
        agent = _agents.get(agent_id)
        os_type = getattr(agent.os_type, "value", str(agent.os_type)) if agent else "linux"
        name = (agent.name if agent else agent_id) or agent_id
        is_win = "windows" in os_type.lower()
        is_android = "android" in os_type.lower()

        # 外网连通性预检：
        # - 目标是境内 IP → 所有节点都参与，不需要预检
        # - 目标是境外 IP → ping 一下，失败就跳过（Windows 无外网时自动跳过）
        if not target_is_cn:
            try:
                # 用 HTTP 请求判断外网连通性，比 ping 更可靠（很多服务器屏蔽 ICMP）
                if is_android:
                    # Android 用 OkHttp（curl 会被 AndroidNetTools 拦截为 OkHttp）
                    # 直接请求 ipinfo，有返回就说明有外网
                    chk = f"curl -s --max-time 5 https://ipinfo.io/ip"
                elif is_win:
                    chk = f"ping -n 1 -w 1000 {target_ip}"
                else:
                    chk = f"ping -c 1 -W 2 {target_ip} 2>/dev/null"

                chk_resp = await _ws_call(agent_id, {"type": "exec", "command": chk, "timeout": 8}, timeout=10)
                chk_out = chk_resp.get("output", "") or ""

                if is_android:
                    # 有返回内容（IP 地址）就说明有外网，错误信息包含 "error" 或 "Unable" 说明无网络
                    if not chk_out.strip() or "error" in chk_out.lower() or "unable" in chk_out.lower() or "failed" in chk_out.lower():
                        return {"agent_id": agent_id, "name": name, "os_type": os_type,
                                "status": "failed", "error": "无外网访问权限，跳过境外目标探测",
                                "hops": [], "total_hops": 0, "valid_hops": 0, "timeout_hops": 0,
                                "private_hops": 0, "last3": [], "all_hops": [], "last_latency": 0}
                else:
                    unreachable = (
                        "100% packet loss" in chk_out or
                        "0 received" in chk_out or
                        "Request timed out" in chk_out or
                        "请求超时" in chk_out or
                        "Destination host unreachable" in chk_out or
                        "无法访问目标主机" in chk_out or
                        (is_win and "TTL" not in chk_out and "ms" not in chk_out)
                    )
                    if unreachable:
                        return {"agent_id": agent_id, "name": name, "os_type": os_type,
                                "status": "failed", "error": "无外网访问权限，跳过境外目标探测",
                                "hops": [], "total_hops": 0, "valid_hops": 0, "timeout_hops": 0,
                                "private_hops": 0, "last3": [], "all_hops": [], "last_latency": 0}
            except Exception:
                # 预检超时也认为无法访问（Windows 快速跳过，Android 和 Linux 继续尝试）
                if is_win:
                    return {"agent_id": agent_id, "name": name, "os_type": os_type,
                            "status": "failed", "error": "预检超时，跳过境外目标探测",
                            "hops": [], "total_hops": 0, "valid_hops": 0, "timeout_hops": 0,
                            "private_hops": 0, "last3": [], "all_hops": [], "last_latency": 0}

        if is_ipv6:
            # IPv6：traceroute6 或 ping6，取延迟为主
            if is_win:
                cmd = f"ping -6 -n 3 {resolved_ip}"
            elif is_android:
                cmd = f"ping6 -c 3 {resolved_ip} 2>/dev/null || ping -c 3 {resolved_ip}"
            else:
                cmd = f"traceroute6 -n -m 15 -w 2 {resolved_ip} 2>/dev/null || ping6 -c 3 {resolved_ip} 2>/dev/null || ping -c 3 {resolved_ip}"
        elif is_win:
            cmd = f"tracert -d -h 20 {resolved_ip}"
        elif is_android:
            cmd = f"ping -c 3 {resolved_ip}"
        else:
            cmd = f"traceroute -n -m 20 -w 2 {resolved_ip} 2>/dev/null || tracepath -n {resolved_ip} 2>/dev/null"

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

    # 5. 把用户检测的 IP 加入 scan_queue（低优先级，已扫过的跳过）
    try:
        scan_ip = resolved_ip if resolved_ip != target_ip else target_ip
        if scan_ip and '.' in scan_ip:  # 只处理 IPv4
            from netcheck.queue import enqueue
            enqueue([scan_ip], source="probe_user", priority=9)  # 9=最低优先级
    except Exception:
        pass


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

        # 外网连通性预检：
        # 目标是境外域名/IP → ping 一下，失败就跳过（Windows 无外网时自动跳过）
        # 目标是境内 → 所有节点参与，不预检
        target_cn = False
        try:
            import socket as _sock
            resolved_ip = _sock.gethostbyname(target)
            # 简单判断是否是中国大陆 IP（通过 ipinfo 已有的 ip_profile 或直接判断）
            # 这里用简单规则：如果能快速 ping 通（<50ms）说明是境内，否则认为境外
        except Exception:
            pass

        try:
            if is_win:
                check_cmd = f"ping -n 1 -w 3000 {target}"
            else:
                check_cmd = f"ping -c 1 -W 3 {target} 2>/dev/null"
            check_resp = await _ws_call(agent_id, {"type": "exec", "command": check_cmd, "timeout": 8}, timeout=10)
            check_out = check_resp.get("output", "") or ""
            if "100% packet loss" in check_out or "100% 丢失" in check_out or \
               ("transmitted" in check_out and "0 received" in check_out) or \
               (is_win and "请求超时" in check_out and "TTL" not in check_out):
                return {"agent_id": agent_id, "name": name, "os_type": os_type,
                        "status": "failed", "error": "无外网访问权限，跳过境外目标探测", "hops": [],
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


# ── 威胁情报查询 ──────────────────────────────────────────────

@router.get("/threat/{ip}")
async def get_threat_intel(ip: str):
    """查询 IP 威胁情报（AbuseIPDB + VirusTotal）"""
    if not _is_safe_target(ip):
        raise HTTPException(400, "目标地址不合法")
    from netcheck.threat_intel import get_threat_intel as _get_intel
    return await _get_intel(ip)


# ── FOFA IP 导入 ──────────────────────────────────────────────

@router.get("/fofa/search")
async def fofa_search(q: str, size: int = 50):
    """从 FOFA 查询 IP 列表，供 batch-scan 使用"""
    import base64, aiohttp, os
    email = os.getenv("FOFA_EMAIL", "")
    key = os.getenv("FOFA_KEY", "")
    if not email or not key:
        raise HTTPException(500, "未配置 FOFA API")
    if size > 200:
        size = 200

    qb64 = base64.b64encode(q.encode()).decode()
    url = f"https://fofa.info/api/v1/search/all?email={email}&key={key}&qbase64={qb64}&size={size}&fields=ip,port,country,org"

    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                d = await r.json()
                if d.get("error"):
                    raise HTTPException(400, d.get("errmsg", "FOFA error"))
                # 去重 IP
                ips = list(dict.fromkeys(row[0] for row in d.get("results", []) if row and row[0]))
                return {
                    "total": d.get("size", 0),
                    "returned": len(ips),
                    "ips": ips,
                    "query": q,
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:100])


# ── 数据统计展示 ──────────────────────────────────────────────

def _table_exists(cursor, table_name: str) -> bool:
    r = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return r is not None

@router.get("/stats")
async def get_stats():
    """返回数据库统计数据，供展示页使用"""
    from netcheck.trace_db import get_conn
    with get_conn() as db:
        c = db.cursor()

        # 总体统计
        total_tasks = c.execute("SELECT COUNT(*) FROM traceroute_tasks").fetchone()[0]
        total_hops = c.execute("SELECT COUNT(*) FROM traceroute_hops").fetchone()[0]
        total_ips = c.execute("SELECT COUNT(*) FROM ip_profiles").fetchone()[0]
        total_targets = c.execute("SELECT COUNT(DISTINCT target) FROM traceroute_tasks").fetchone()[0]
        total_countries = c.execute("SELECT COUNT(DISTINCT country) FROM ip_profiles WHERE country != ''").fetchone()[0]

        # ASN 分布（出现最多的上游）
        asn_rows = c.execute("""
            SELECT asn, org, COUNT(*) as cnt FROM traceroute_hops
            WHERE asn != '' AND is_private=0
            GROUP BY asn ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        asn_dist = [{"asn": r[0], "org": (r[1] or "").split(" ", 1)[-1][:30], "count": r[2]} for r in asn_rows]

        # IP 类型分布
        tag_rows = c.execute("""
            SELECT tag, COUNT(DISTINCT ip) as cnt FROM ip_profiles
            WHERE tag != '' GROUP BY tag ORDER BY cnt DESC
        """).fetchall()
        tag_dist = [{"tag": r[0], "count": r[1]} for r in tag_rows]

        # 国家分布
        country_rows = c.execute("""
            SELECT country, COUNT(DISTINCT ip) as cnt FROM ip_profiles
            WHERE country != '' GROUP BY country ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        country_dist = [{"country": r[0], "count": r[1]} for r in country_rows]

        # 最近10条探测记录
        recent_rows = c.execute("""
            SELECT t.target, t.agent_name, t.os_type, t.total_hops, t.created_at,
                   h.country, h.city, h.org, h.tag
            FROM traceroute_tasks t
            LEFT JOIN traceroute_hops h ON h.task_id=t.task_id AND h.is_last_hop=1
            ORDER BY t.created_at DESC LIMIT 15
        """).fetchall()
        recent = [{
            "target": r[0], "agent": r[1] or "", "os": r[2] or "",
            "hops": r[3], "time": r[4][:16] if r[4] else "",
            "country": r[5] or "", "city": r[6] or "",
            "org": (r[7] or "")[:25], "tag": r[8] or ""
        } for r in recent_rows]

        # 节点参与统计
        node_rows = c.execute("""
            SELECT agent_name, os_type, COUNT(*) as cnt
            FROM traceroute_tasks GROUP BY agent_id ORDER BY cnt DESC
        """).fetchall()
        nodes = [{"name": r[0] or "unknown", "os": r[1] or "", "count": r[2]} for r in node_rows]

        # ASN 集群分析结果
        import json as _json
        cluster_rows = c.execute("""
            SELECT key, value FROM analysis_results WHERE type='asn_cluster'
            ORDER BY json_extract(value, '$.hop_count') DESC LIMIT 10
        """).fetchall() if _table_exists(c, 'analysis_results') else []
        asn_clusters = [_json.loads(r[1]) for r in cluster_rows]

        # 调度器任务状态
        job_rows = c.execute(
            "SELECT name, last_run FROM scheduler_jobs ORDER BY last_run DESC"
        ).fetchall() if _table_exists(c, 'scheduler_jobs') else []
        scheduler_jobs = [{"name": r[0], "last_run": r[1][:16] if r[1] else ""} for r in job_rows]

        # 队列状态
        from netcheck.queue import queue_stats
        q_stats = queue_stats()

    return {
        "summary": {
            "total_tasks": total_tasks,
            "total_hops": total_hops,
            "total_ips": total_ips,
            "total_targets": total_targets,
            "total_countries": total_countries,
        },
        "queue": q_stats,
        "asn_distribution": asn_dist,
        "tag_distribution": tag_dist,
        "country_distribution": country_dist,
        "recent_scans": recent,
        "nodes": nodes,
        "asn_clusters": asn_clusters,
        "scheduler_jobs": scheduler_jobs,
    }


@router.get("/ip-profiles")
async def get_ip_profiles(tag: str = "", country: str = "", asn: str = "", limit: int = 50):
    """按条件查询 IP 画像列表"""
    from netcheck.trace_db import get_conn
    with get_conn() as db:
        c = db.cursor()
        where = []
        params = []
        if tag:
            where.append("tag LIKE ?")
            params.append(f"%{tag}%")
        if country:
            where.append("country=?")
            params.append(country)
        if asn:
            where.append("asn=?")
            params.append(asn)
        sql = "SELECT ip, asn, org, country, city, tag, seen_count, first_seen, last_seen FROM ip_profiles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seen_count DESC LIMIT ?"
        params.append(limit)
        rows = c.execute(sql, params).fetchall()
        return [{"ip": r[0], "asn": r[1], "org": r[2], "country": r[3],
                 "city": r[4], "tag": r[5], "seen_count": r[6],
                 "first_seen": (r[7] or "")[:10], "last_seen": (r[8] or "")[:10]} for r in rows]


# ── 路径收敛分析 ──────────────────────────────────────────────

@router.get("/convergence/summary")
async def get_convergence_summary():
    """获取路径收敛分析汇总"""
    from netcheck.convergence import get_convergence_summary
    return get_convergence_summary()


@router.post("/convergence/analyze")
async def trigger_analysis():
    """手动触发批量收敛分析"""
    import asyncio
    from netcheck.convergence import run_batch_analysis
    asyncio.create_task(asyncio.to_thread(run_batch_analysis, 2))
    return {"status": "started"}


@router.get("/convergence/target/{ip}")
async def get_target_convergence(ip: str):
    """查询单个 IP 的收敛分析结果"""
    from netcheck.convergence import analyze_target
    return analyze_target(ip)


# ── 端口扫描结果 ──────────────────────────────────────────────

@router.get("/portscan/search")
async def search_portscan(q: str = "", limit: int = 100):
    """
    搜索端口扫描数据，支持：
    - ip=1.2.3.4 或直接输入 IP
    - port=8388 或直接输入端口号
    - port=8388,443 多端口
    - gateway=45.207.215.1
    - profile=full_proxy
    """
    from netcheck.trace_db import get_conn
    import json as _json, re as _re

    q = q.strip()
    if not q:
        # 无查询返回统计
        with get_conn() as db:
            c = db.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='port_scan_results'")
            if not c.fetchone():
                return {"results": [], "total": 0, "query_type": "empty"}
            rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results ORDER BY port_count DESC LIMIT ?", (limit,)).fetchall()
            total = c.execute("SELECT COUNT(*) FROM port_scan_results").fetchone()[0]
        return {"results": _fmt_rows(rows), "total": total, "query_type": "all"}

    with get_conn() as db:
        c = db.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='port_scan_results'")
        if not c.fetchone():
            return {"results": [], "total": 0, "query_type": "no_data"}

        # 解析查询
        query_type = "unknown"
        rows = []

        # ip= 或纯 IP
        ip_match = _re.match(r'^(?:ip=)?(\d+\.\d+\.\d+\.\d+(?:/\d+)?)$', q)
        if ip_match:
            ip_val = ip_match.group(1)
            if '/' in ip_val:
                # CIDR 查询
                import ipaddress
                try:
                    net = ipaddress.ip_network(ip_val, strict=False)
                    rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results").fetchall()
                    rows = [r for r in rows if ipaddress.ip_address(r[0]) in net][:limit]
                    query_type = "cidr"
                except Exception:
                    rows = []
            else:
                rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results WHERE ip LIKE ?", (f"%{ip_val}%",)).fetchall()[:limit]
                query_type = "ip"

        # protocol= 协议名搜索
        elif q.startswith('protocol=') or q.lower() in ('shadowsocks','ss','v2ray','xray','clash','socks5','vmess','trojan'):
            proto = q.replace('protocol=', '').lower().strip()
            PROTO_PORTS = {
                'shadowsocks': ['8388'], 'ss': ['8388'],
                'v2ray': ['10086', '10808'], 'vmess': ['10086', '10808'],
                'xray': ['10808', '10086'],
                'clash': ['7890'],
                'socks5': ['1080'],
                'trojan': ['443'],
                'ssh': ['22'],
            }
            ports = PROTO_PORTS.get(proto, [])
            if not ports:
                rows = []
                query_type = "protocol"
            else:
                all_rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results").fetchall()
                rows = [r for r in all_rows if any(p in set(_json.loads(r[1]) if r[1] else []) for p in ports)]
                rows = sorted(rows, key=lambda x: -x[2])[:limit]
                query_type = "protocol"

        # port= 或纯数字
        elif _re.match(r'^(?:port=)?[\d,]+$', q):
            ports_str = q.replace('port=', '')
            ports = [p.strip() for p in ports_str.split(',') if p.strip()]
            # 查包含所有指定端口的 IP
            all_rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results").fetchall()
            rows = []
            for r in all_rows:
                open_p = set(_json.loads(r[1]) if r[1] else [])
                if all(p in open_p for p in ports):
                    rows.append(r)
            rows = sorted(rows, key=lambda x: -x[2])[:limit]
            query_type = "port"

        # gateway=
        elif q.startswith('gateway='):
            gw = q.replace('gateway=', '').strip()
            rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results WHERE gateway_ip=? ORDER BY port_count DESC LIMIT ?", (gw, limit)).fetchall()
            query_type = "gateway"

        # profile=
        elif q.startswith('profile='):
            pf = q.replace('profile=', '').strip()
            rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results WHERE profile=? ORDER BY port_count DESC LIMIT ?", (pf, limit)).fetchall()
            query_type = "profile"

        else:
            # 模糊搜索 IP
            rows = c.execute("SELECT ip,open_ports,port_count,profile,gateway_ip,scanned_by,scanned_at FROM port_scan_results WHERE ip LIKE ? ORDER BY port_count DESC LIMIT ?", (f"%{q}%", limit)).fetchall()
            query_type = "fuzzy"

    return {"results": _fmt_rows(rows), "total": len(rows), "query_type": query_type, "query": q}


def _fmt_rows(rows):
    import json as _json
    from netcheck.trace_db import get_conn
    ips = [r[0] for r in rows]
    if not ips:
        return []

    with get_conn() as db:
        ph = ','.join('?' * len(ips))

        # ip_profiles: 地理、ASN、类型
        geo = {}
        for g in db.execute(f"SELECT ip,country,city,org,asn,tag,seen_count FROM ip_profiles WHERE ip IN ({ph})", ips).fetchall():
            geo[g[0]] = {"country": g[1] or "", "city": g[2] or "", "org": g[3] or "", "asn": g[4] or "", "tag": g[5] or "", "seen_count": g[6] or 0}

        # convergence_results: 收敛分析
        conv = {}
        for g in db.execute(f"SELECT target,convergence_ip,convergence_org,convergence_hop,node_count,confidence,tag FROM convergence_results WHERE target IN ({ph})", ips).fetchall():
            conv[g[0]] = {"convergence_ip": g[1], "convergence_org": g[2] or "", "convergence_hop": g[3], "node_count": g[4], "confidence": g[5], "tag": g[6]}

        # traceroute_tasks: 最近一次各节点延迟
        traces = {}
        for g in db.execute(f"SELECT target,agent_name,total_hops,last_latency_ms FROM traceroute_tasks WHERE target IN ({ph}) ORDER BY created_at DESC", ips).fetchall():
            if g[0] not in traces:
                traces[g[0]] = []
            if len(traces[g[0]]) < 3:
                traces[g[0]].append({"agent": g[1] or "", "hops": g[2] or 0, "latency": g[3] or 0})

    return [{
        "ip": r[0],
        "open_ports": _json.loads(r[1]) if r[1] else [],
        "port_count": r[2],
        "profile": r[3],
        "gateway_ip": r[4],
        "scanned_by": r[5],
        "scanned_at": (r[6] or "")[:16],
        # 地理信息
        "country": geo.get(r[0], {}).get("country", ""),
        "city": geo.get(r[0], {}).get("city", ""),
        "org": geo.get(r[0], {}).get("org", ""),
        "asn": geo.get(r[0], {}).get("asn", ""),
        "ip_tag": geo.get(r[0], {}).get("tag", ""),
        "seen_count": geo.get(r[0], {}).get("seen_count", 0),
        # 收敛分析
        "convergence": conv.get(r[0]),
        # 路由数据
        "traces": traces.get(r[0], []),
    } for r in rows]


@router.get("/portscan/results")
async def get_portscan_results(gateway: str = "", profile: str = "", limit: int = 500):
    """查询端口扫描结果"""
    return await search_portscan(q=f"gateway={gateway}" if gateway else (f"profile={profile}" if profile else ""), limit=limit)


# ── 手动加入扫描队列 ──────────────────────────────────────────

from pydantic import BaseModel as _BM3

class QueueAddRequest(_BM3):
    ips: list
    priority: int = 5
    source: str = "manual"

@router.post("/queue/add")
async def add_to_queue(req: QueueAddRequest):
    """手动把 IP 列表加入扫描队列"""
    from netcheck.queue import enqueue, queue_stats
    # 过滤有效 IP/域名
    valid = [ip.strip() for ip in req.ips if ip.strip() and len(ip.strip()) > 3]
    added = enqueue(valid, source=req.source, priority=req.priority)
    stats = queue_stats()
    return {"added": added, "total_submitted": len(valid), "queue": stats}

@router.get("/queue/stats")
async def get_queue_stats():
    """获取队列状态"""
    from netcheck.queue import queue_stats
    return queue_stats()


# ── 独立站情报 ────────────────────────────────────────────────

from pydantic import BaseModel as _BM4

class EcomSearchRequest(_BM4):
    keyword: str
    limit: int = 10
    require_tiktok: bool = False   # 只返回有 TikTok 账号的站
    require_tt_ads: bool = False   # 只返回在投 TikTok 广告的站（有 Pixel）

class EcomSingleRequest(_BM4):
    domain: str  # 直接分析单个域名

@router.post("/ecom/analyze")
async def ecom_analyze_single(req: EcomSingleRequest):
    """直接分析单个独立站"""
    import aiohttp, ssl as _ssl, re as _re
    from datetime import datetime as _dt

    domain = req.domain.strip()
    # 正确去掉协议前缀
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
            break
    domain = domain.split("/")[0]  # 去掉路径部分
    ssl_ctx = _ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = _ssl.CERT_NONE

    site = {"ip": "", "domain": domain, "title": "", "country": "", "city": "", "server": ""}
    result = {**site, "platform": "未知", "cdn": "未知", "payment": [], "tech_stack": [], "social": {}, "price_hint": "", "analyzed_at": _dt.now().isoformat()[:16]}

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
            async with s.get(f"https://{domain}", headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as r:
                headers = dict(r.headers)
                body = await r.text(errors='ignore')
                body = body[:150000]  # 取前150KB，确保覆盖footer社交链接

                # 平台
                if "_shopify" in str(headers).lower() or "cdn.shopify.com" in body or "Shopify.theme" in body:
                    result["platform"] = "Shopify"
                elif "woocommerce" in body.lower() or "wp-content" in body:
                    result["platform"] = "WooCommerce"
                elif "bigcommerce" in body.lower():
                    result["platform"] = "BigCommerce"
                elif headers.get("x-powered-by", "").lower().startswith("next"):
                    result["platform"] = "Next.js"
                elif "squarespace" in body.lower():
                    result["platform"] = "Squarespace"

                # CDN
                if "cf-ray" in str(headers).lower() or headers.get("server", "").lower() == "cloudflare":
                    result["cdn"] = "Cloudflare"
                elif "cloudfront" in str(headers).lower() or "x-amz-cf" in str(headers).lower():
                    result["cdn"] = "CloudFront"
                elif headers.get("server", "").lower() == "vercel":
                    result["cdn"] = "Vercel"
                elif headers.get("server", "").lower() == "netlify":
                    result["cdn"] = "Netlify"
                elif "fastly" in str(headers).lower():
                    result["cdn"] = "Fastly"

                # 支付
                if "stripe" in body.lower(): result["payment"].append("Stripe")
                if "paypal" in body.lower(): result["payment"].append("PayPal")
                if "klarna" in body.lower(): result["payment"].append("Klarna")
                if "afterpay" in body.lower(): result["payment"].append("Afterpay")
                if "shop pay" in body.lower() or "shop_pay" in body.lower(): result["payment"].append("Shop Pay")

                # 技术栈
                if "react" in body.lower() and "react-dom" in body.lower(): result["tech_stack"].append("React")
                if "gtag" in body or "google-analytics" in body: result["tech_stack"].append("Google Analytics")
                if "facebook.net/en_US/fbevents" in body: result["tech_stack"].append("Facebook Pixel")
                if "klaviyo" in body.lower(): result["tech_stack"].append("Klaviyo")
                if "analytics.tiktok.com" in body or "ttq." in body: result["tech_stack"].append("TikTok Pixel")

                # 社交媒体（兼容转义斜杠 \/ 格式）
                _body_unesc = body.replace('\\/', '/')
                social = {}
                tt = _re.findall(r'tiktok\.com/@([\w.]+)', _body_unesc)
                if tt: social["tiktok"] = f"@{tt[0]}"
                ig = _re.findall(r'instagram\.com/([\w.]+)', _body_unesc)
                ig = [x for x in ig if x not in ('p','reel','stories','explore','accounts','_u','sharer')]
                if ig: social["instagram"] = f"@{ig[0]}"
                yt = _re.findall(r'youtube\.com/@([\w.]+)', _body_unesc)
                if yt: social["youtube"] = f"@{yt[0]}"
                fb = _re.findall(r'facebook\.com/([\w.]+)', _body_unesc)
                fb = [x for x in fb if x not in ('tr','sharer','share','dialog','plugins','login','v2.0','v3.0','policy')]
                if fb: social["facebook"] = fb[0]
                fb_pixel = _re.findall(r'facebook\.com/tr\?id=(\d+)', body)
                if fb_pixel: social["fb_pixel_id"] = fb_pixel[0]
                tt_pixel = _re.findall(r'ttq\.load\(["\']([A-Z0-9]+)["\']\)', body)
                if not tt_pixel:
                    tt_pixel = _re.findall(r'["\']pixelCode["\']\s*:\s*["\']([A-Z0-9]+)["\']', body)
                if tt_pixel: social["tt_pixel_id"] = tt_pixel[0]
                result["social"] = social

                # 价格
                price_matches = _re.findall(r'\$[\d,]+\.?\d*', body)
                if price_matches:
                    prices = sorted(set(price_matches), key=lambda x: float(x.replace('$','').replace(',','')))
                    result["price_hint"] = " / ".join(prices[:5])

                result["server"] = headers.get("server", "")
                # 提取 title
                title_m = _re.search(r'<title[^>]*>([^<]+)</title>', body, _re.IGNORECASE)
                if title_m: result["title"] = title_m.group(1).strip()[:60]

                # Shopify Apps 识别
                _app_sigs = {
                    "ReCharge": ["rechargeapps.com", "rechargepayments.com"],
                    "Loox": ["loox.io", "loox-reviews"],
                    "Yotpo": ["yotpo.com", "yotpoWidget"],
                    "Okendo": ["okendo.io"],
                    "Stamped": ["stamped.io"],
                    "Judge.me": ["judge.me"],
                    "Privy": ["privy.com", "widget.privy"],
                    "Postscript": ["postscript.io"],
                    "Attentive": ["attn.tv", "attentivemobile"],
                    "Hotjar": ["hotjar.com"],
                    "Lucky Orange": ["luckyorange.com"],
                }
                result["shopify_apps"] = [app for app, sigs in _app_sigs.items() if any(sig in body for sig in sigs)]

    except Exception as e:
        result["error"] = str(e)[:80]

    # 并发跑商品数 + 建站时间
    import asyncio as _asyncio
    async def _prod_count(domain):
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                async with s.get(f"https://{domain}/sitemap.xml", headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=4)) as r:
                    if r.status == 200:
                        text = await r.text(errors='ignore')
                        n = len(_re.findall(r'sitemap_products_\d+\.xml', text))
                        if n > 0: return n * 250
        except Exception:
            pass
        return 0

    async def _reg_date(domain):
        try:
            tld = domain.split('.')[-1]
            root = '.'.join(domain.split('.')[-2:])
            servers = {'com':'https://rdap.verisign.com/com/v1','net':'https://rdap.verisign.com/net/v1'}
            base = servers.get(tld, 'https://rdap.org')
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                async with s.get(f"{base}/domain/{root}", headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=4)) as r:
                    if r.status == 200:
                        d = await r.json(content_type=None)
                        for ev in d.get("events", []):
                            if "registration" in ev.get("eventAction", ""):
                                return ev.get("eventDate", "")[:10]
        except Exception:
            pass
        return ""

    pc, rd = await _asyncio.gather(_prod_count(domain), _reg_date(domain))
    result["product_count"] = pc
    result["registered_at"] = rd
    result["traffic"] = {}

    # 自动缓存
    try:
        from netcheck.trace_db import save_ecom_site
        if result.get("platform") != "未知":
            save_ecom_site(result, category="")
    except Exception:
        pass

    return result


@router.get("/ecom/db")
async def ecom_db_query(category: str = "", has_tiktok: bool = False,
                        platform: str = "", limit: int = 100):
    """查询缓存的独立站情报数据库"""
    from netcheck.trace_db import get_ecom_sites
    sites = get_ecom_sites(category=category, limit=limit,
                           has_tiktok=has_tiktok, platform=platform)
    return {
        "total": len(sites),
        "category": category,
        "sites": sites,
    }


@router.get("/ecom/db/stats")
async def ecom_db_stats():
    """独立站数据库统计"""
    from netcheck.trace_db import get_conn
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM ecom_sites").fetchone()[0]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM ecom_sites WHERE category != '' GROUP BY category ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        by_platform = conn.execute(
            "SELECT platform, COUNT(*) as cnt FROM ecom_sites GROUP BY platform ORDER BY cnt DESC"
        ).fetchall()
        with_tiktok = conn.execute(
            "SELECT COUNT(*) FROM ecom_sites WHERE social LIKE '%\"tiktok\"%'"
        ).fetchone()[0]
        with_fb_ads = conn.execute(
            "SELECT COUNT(*) FROM ecom_sites WHERE social LIKE '%fb_pixel_id%'"
        ).fetchone()[0]
    return {
        "total": total,
        "with_tiktok": with_tiktok,
        "with_fb_ads": with_fb_ads,
        "by_category": [{"category": r[0], "count": r[1]} for r in by_cat],
        "by_platform": [{"platform": r[0], "count": r[1]} for r in by_platform],
    }


@router.get("/ecom/traffic")
async def ecom_traffic(domain: str):
    """按需获取 SimilarWeb 流量数据（用户点击时才调用）"""
    import aiohttp, ssl as _ssl, re as _re

    ssl_ctx = _ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = _ssl.CERT_NONE

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
            async with s.get(
                f"https://www.similarweb.com/website/{domain}/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                text = await r.text(errors='ignore')

                # 提取月访问量
                visits = ""
                m = _re.search(r'"totalVisits"\s*:\s*([\d.]+)', text)
                if m:
                    v = float(m.group(1))
                    if v >= 1_000_000: visits = f"{v/1_000_000:.1f}M"
                    elif v >= 1_000: visits = f"{v/1_000:.0f}K"
                    else: visits = str(int(v))
                if not visits:
                    m = _re.search(r'([\d,.]+)\s*(?:Total Visits|Monthly Visits)', text)
                    if m: visits = m.group(1).replace(',','')

                # 全球排名
                rank = 0
                m = _re.search(r'"globalRank"\s*:\s*\{"rank"\s*:\s*(\d+)', text)
                if m: rank = int(m.group(1))

                # 跳出率
                bounce = ""
                m = _re.search(r'"bounceRate"\s*:\s*([\d.]+)', text)
                if m: bounce = f"{float(m.group(1))*100:.0f}%"

                # 平均访问时长
                duration = ""
                m = _re.search(r'"avgVisitDuration"\s*:\s*([\d.]+)', text)
                if m:
                    sec = int(float(m.group(1)))
                    duration = f"{sec//60}m{sec%60}s"

                if visits or rank:
                    return {
                        "domain": domain,
                        "monthly_visits": visits,
                        "global_rank": rank,
                        "bounce_rate": bounce,
                        "avg_duration": duration,
                        "source": "similarweb",
                    }
                # SimilarWeb 返回了但没数据（JS渲染）
                return {"domain": domain, "monthly_visits": "", "global_rank": 0, "note": "需要JS渲染，数据不可用"}
    except Exception as e:
        return {"domain": domain, "error": str(e)[:60]}

@router.post("/ecom/search")
async def ecom_search(req: EcomSearchRequest):
    """搜索独立站并分析基础设施"""
    import base64, aiohttp, ssl as _ssl, re as _re, os as _os
    from datetime import datetime as _dt

    keyword = req.keyword.strip()
    limit = min(req.limit, 50)
    fofa_fetch = min(limit * 5, 200)  # FOFA 多拉5倍去重，最多200条

    # 1. FOFA 搜索独立站
    email = _os.getenv("FOFA_EMAIL", "")
    key = _os.getenv("FOFA_KEY", "")
    fofa_sites = []

    if email and key:
        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        # 统一查询，不在 FOFA 层过滤（FOFA 快照和实时抓取内容可能不一致）
        queries = [
            f'body="cdn.shopify.com" && title="{keyword}" && status_code="200" && type="subdomain"',
            f'title="{keyword}" && (body="woocommerce" || body="wp-content/plugins") && status_code="200" && type="subdomain"',
        ]
        seen = set()
        for q in queries:
            if len(fofa_sites) >= limit:
                break
            qb64 = base64.b64encode(q.encode()).decode()
            url = f"https://fofa.info/api/v1/search/all?email={email}&key={key}&qbase64={qb64}&size={fofa_fetch}&fields=ip,domain,title,country,city,server"
            try:
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                    async with s.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                        d = await r.json()
                        for row in d.get("results", []):
                            ip, domain, title, country, city, server = row
                            # 过滤：只要有真实域名、排除 IP 直接访问、排除 myshopify.com 子域
                            if not domain or domain in seen:
                                continue
                            if "myshopify.com" in domain or not "." in domain:
                                continue
                            seen.add(domain)
                            fofa_sites.append({"ip": ip, "domain": domain, "title": (title or "").strip(), "country": country or "", "city": city or "", "server": server or ""})
            except Exception:
                pass

    if not fofa_sites:
        # FOFA 没结果，按关键词给预置知名站
        _fallback_map = {
            "phone case": [
                {"domain": "casetify.com", "title": "CASETiFY"},
                {"domain": "dbrand.com", "title": "dbrand"},
                {"domain": "otterbox.com", "title": "OtterBox"},
                {"domain": "shakercase.com", "title": "ShakerCase"},
                {"domain": "casely.com", "title": "Casely"},
            ],
            "sneakers": [
                {"domain": "allbirds.com", "title": "Allbirds"},
                {"domain": "veja-store.com", "title": "Veja"},
            ],
            "skincare": [
                {"domain": "glossier.com", "title": "Glossier"},
                {"domain": "tatcha.com", "title": "Tatcha"},
            ],
        }
        # 模糊匹配关键词
        kw_lower = keyword.lower()
        fallback = []
        for k, v in _fallback_map.items():
            if k in kw_lower or kw_lower in k:
                fallback = v
                break
        if not fallback:
            fallback = _fallback_map["phone case"]
        fofa_sites = [{"ip": "", "country": "US", "city": "", "server": "", **f} for f in fallback]

    # 2. 批量分析每个站的 HTTP 头
    ssl_ctx = _ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = _ssl.CERT_NONE

    async def analyze_site(site: dict) -> dict:
        domain = site["domain"]
        result = {**site, "platform": "未知", "cdn": "未知", "payment": [], "tech_stack": [], "social": {}, "price_hint": "", "product_count": 0, "registered_at": "", "traffic": {}, "shopify_apps": [], "analyzed_at": _dt.now().isoformat()[:16]}
        _body_cache = ""
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                async with s.get(f"https://{domain}", headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=8), allow_redirects=True) as r:
                    headers = dict(r.headers)
                    body = await r.text(errors='ignore')
                    body = body[:150000]  # 取前150KB，确保覆盖footer社交链接
                    _body_cache = body

                    # 识别平台
                    if "_shopify" in str(headers).lower() or "cdn.shopify.com" in body or "Shopify.theme" in body:
                        result["platform"] = "Shopify"
                    elif "woocommerce" in body.lower() or "wp-content" in body:
                        result["platform"] = "WooCommerce"
                    elif "bigcommerce" in body.lower():
                        result["platform"] = "BigCommerce"
                    elif headers.get("x-powered-by", "").lower().startswith("next"):
                        result["platform"] = "Next.js"
                    elif "squarespace" in body.lower():
                        result["platform"] = "Squarespace"

                    # 识别 CDN
                    if "cf-ray" in str(headers).lower() or headers.get("server", "").lower() == "cloudflare":
                        result["cdn"] = "Cloudflare"
                        result["ip"] = result["ip"] or headers.get("cf-ray", "")[:8]
                    elif "cloudfront" in str(headers).lower() or "x-amz-cf" in str(headers).lower():
                        result["cdn"] = "CloudFront"
                    elif headers.get("server", "").lower() == "vercel":
                        result["cdn"] = "Vercel"
                    elif headers.get("server", "").lower() == "netlify":
                        result["cdn"] = "Netlify"
                    elif "fastly" in str(headers).lower():
                        result["cdn"] = "Fastly"

                    # 识别支付方式
                    if "stripe" in body.lower() or "stripe.com/v3" in body:
                        result["payment"].append("Stripe")
                    if "paypal" in body.lower():
                        result["payment"].append("PayPal")
                    if "klarna" in body.lower():
                        result["payment"].append("Klarna")
                    if "afterpay" in body.lower():
                        result["payment"].append("Afterpay")
                    if "shop pay" in body.lower() or "shop_pay" in body.lower():
                        result["payment"].append("Shop Pay")

                    # 识别技术栈
                    if "react" in body.lower() and "react-dom" in body.lower():
                        result["tech_stack"].append("React")
                    if "vue" in body.lower() and "vue.js" in body.lower():
                        result["tech_stack"].append("Vue")
                    if "gtag" in body or "google-analytics" in body:
                        result["tech_stack"].append("Google Analytics")
                    if "facebook.net/en_US/fbevents" in body:
                        result["tech_stack"].append("Facebook Pixel")
                    if "klaviyo" in body.lower():
                        result["tech_stack"].append("Klaviyo")
                    if "gorgias" in body.lower():
                        result["tech_stack"].append("Gorgias客服")
                    if "analytics.tiktok.com" in body or "ttq." in body:
                        result["tech_stack"].append("TikTok Pixel")

                    # 提取社交媒体账号（兼容转义斜杠 \/ 格式）
                    _body_unesc = body.replace('\\/', '/')
                    social = {}
                    tt = _re.findall(r'tiktok\.com/@([\w.]+)', _body_unesc)
                    if tt: social["tiktok"] = f"@{tt[0]}"
                    ig = _re.findall(r'instagram\.com/([\w.]+)', _body_unesc)
                    ig = [x for x in ig if x not in ('p','reel','stories','explore','accounts','_u','sharer')]
                    if ig: social["instagram"] = f"@{ig[0]}"
                    yt = _re.findall(r'youtube\.com/@([\w.]+)', _body_unesc)
                    if yt: social["youtube"] = f"@{yt[0]}"
                    fb = _re.findall(r'facebook\.com/([\w.]+)', _body_unesc)
                    fb = [x for x in fb if x not in ('tr','sharer','share','dialog','plugins','login','v2.0','v3.0','policy')]
                    if fb: social["facebook"] = fb[0]
                    # Facebook Pixel ID
                    fb_pixel = _re.findall(r'facebook\.com/tr\?id=(\d+)', body)
                    if fb_pixel: social["fb_pixel_id"] = fb_pixel[0]
                    # TikTok Pixel ID
                    tt_pixel = _re.findall(r'ttq\.load\(["\']([A-Z0-9]+)["\']\)', body)
                    if not tt_pixel:
                        tt_pixel = _re.findall(r'["\']pixelCode["\']\s*:\s*["\']([A-Z0-9]+)["\']', body)
                    if tt_pixel: social["tt_pixel_id"] = tt_pixel[0]
                    result["social"] = social

                    # 提取价格提示（找页面里的价格）
                    price_matches = _re.findall(r'\$[\d,]+\.?\d*', body)
                    if price_matches:
                        prices = sorted(set(price_matches), key=lambda x: float(x.replace('$','').replace(',','')))
                        result["price_hint"] = " / ".join(prices[:5])

                    # 更新 server
                    result["server"] = headers.get("server", result.get("server", ""))

        except Exception as e:
            result["error"] = str(e)[:50]

        # ── 并发跑额外分析（不阻塞主流程）──────────────────
        async def _get_product_count(domain):
            """用 Shopify products.json 估算商品数（公开接口，0.3s）"""
            try:
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                    # 先试 products.json（Shopify 通用）
                    async with s.get(f"https://{domain}/products.json?limit=1",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            # 从 sitemap 索引里数子 sitemap 数量估算规模
                            # products.json 只返回当页，用 sitemap 数子链接数
                            pass
                    # 抓 sitemap.xml 解析子 sitemap 数量
                    async with s.get(f"https://{domain}/sitemap.xml",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            text = await r.text(errors='ignore')
                            # 数 sitemap_products_N.xml 的数量，每个约250个商品
                            prod_sitemaps = len(_re.findall(r'sitemap_products_\d+\.xml', text))
                            if prod_sitemaps > 0:
                                return prod_sitemaps * 250  # 估算
                            # 直接数 <loc> 里含 /products/ 的
                            prod_urls = len(_re.findall(r'/products/', text))
                            if prod_urls > 0:
                                return prod_urls
            except Exception:
                pass
            # fallback: products.json count
            try:
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                    async with s.get(f"https://{domain}/collections/all/products.json?limit=250",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            d = await r.json(content_type=None)
                            return len(d.get("products", []))
            except Exception:
                pass
            return 0

        async def _get_reg_date(domain):
            """用 verisign RDAP 查建站时间（0.2s，准确）"""
            try:
                root = '.'.join(domain.split('.')[-2:])
                tld = domain.split('.')[-1]
                rdap_servers = {
                    'com': 'https://rdap.verisign.com/com/v1',
                    'net': 'https://rdap.verisign.com/net/v1',
                    'org': 'https://rdap.publicinterestregistry.org/rdap',
                    'io':  'https://rdap.nic.io',
                    'co':  'https://rdap.nic.co',
                }
                base = rdap_servers.get(tld, 'https://rdap.org')
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                    async with s.get(f"{base}/domain/{root}",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=4)) as r:
                        if r.status == 200:
                            d = await r.json(content_type=None)
                            for ev in d.get("events", []):
                                if "registration" in ev.get("eventAction", ""):
                                    return ev.get("eventDate", "")[:10]
            except Exception:
                pass
            return ""

        # 并发跑两个额外分析（去掉 SimilarWeb，太慢且需要JS渲染）
        prod_count, reg_date = await asyncio.gather(
            _get_product_count(domain),
            _get_reg_date(domain),
        )

        result["product_count"] = prod_count
        result["registered_at"] = reg_date
        result["traffic"] = {}  # SimilarWeb 需要付费API，暂不支持

        # Shopify App 识别（从已抓的 body 里提取）
        shopify_apps = []
        _app_sigs = {
            "ReCharge": ["rechargeapps.com", "rechargepayments.com"],
            "Loox": ["loox.io", "loox-reviews"],
            "Yotpo": ["yotpo.com", "yotpoWidget"],
            "Okendo": ["okendo.io"],
            "Stamped": ["stamped.io"],
            "Judge.me": ["judge.me"],
            "Privy": ["privy.com", "widget.privy"],
            "SMSBump": ["smsbump.com"],
            "Postscript": ["postscript.io"],
            "Attentive": ["attn.tv", "attentivemobile"],
            "LimeSpot": ["limespot.com"],
            "Bold": ["boldapps.net"],
            "Upsell": ["zipify.com", "carthook.com"],
            "Hotjar": ["hotjar.com"],
            "Lucky Orange": ["luckyorange.com"],
        }
        body_lower = result.get("_body_cache", "")
        for app, sigs in _app_sigs.items():
            if any(sig in body for sig in sigs):
                shopify_apps.append(app)
        result["shopify_apps"] = shopify_apps

        return result

    import asyncio

    # ── 先查缓存，已有的域名不重复分析 ──────────────────────
    from netcheck.trace_db import get_ecom_sites, save_ecom_site, get_ecom_site
    cached = get_ecom_sites(category=keyword, limit=limit)
    cached_domains = {s["domain"] for s in cached}

    # FOFA 结果去掉已缓存的域名，只分析新的
    new_sites = [s for s in fofa_sites[:limit * 2] if s["domain"] not in cached_domains]
    need_count = max(0, limit - len(cached))  # 还需要几条新的

    tasks = [analyze_site(s) for s in new_sites[:need_count]]
    new_results = await asyncio.gather(*tasks) if tasks else []

    # 自动缓存新分析的站
    for r in new_results:
        if r.get("domain") and r.get("platform") != "未知":
            save_ecom_site(r, category=keyword)

    # 合并：缓存 + 新分析，去重
    merged = {s["domain"]: s for s in cached}
    for r in new_results:
        merged[r["domain"]] = r
    results = list(merged.values())[:limit]

    return {
        "keyword": keyword,
        "total": len(results),
        "from_cache": len(cached),
        "from_fofa": len(new_results),
        "sites": results,
        "analyzed_at": _dt.now().isoformat()[:16],
    }


# ── 热门品类预热 ──────────────────────────────────────────────

# TikTok 热门品类列表（与前端保持一致）
_HOT_CATEGORIES = [
    "phone case", "skincare", "sneakers", "fitness",
    "pet supplies", "jewelry", "fashion", "home decor",
    "gaming accessories", "supplements", "hair care",
]

@router.post("/ecom/warmup")
async def ecom_warmup(background_tasks=None):
    """后台预热：抓取所有热门品类的独立站数据并缓存"""
    import asyncio as _asyncio
    from netcheck.trace_db import get_ecom_sites

    async def warmup_one(kw: str):
        from netcheck.trace_db import get_ecom_sites
        existing = get_ecom_sites(category=kw, limit=1)
        if existing:
            return {"keyword": kw, "status": "skipped", "cached": len(get_ecom_sites(category=kw, limit=100))}
        # 模拟搜索请求
        req = EcomSearchRequest(keyword=kw, limit=20, require_tiktok=False, require_tt_ads=False)
        try:
            result = await ecom_search(req)
            return {"keyword": kw, "status": "done", "total": result.get("total", 0)}
        except Exception as e:
            return {"keyword": kw, "status": "error", "error": str(e)[:50]}

    # 串行执行，避免并发太多请求
    results = []
    for kw in _HOT_CATEGORIES:
        r = await warmup_one(kw)
        results.append(r)
        await _asyncio.sleep(2)  # 每个品类间隔2秒，避免被封

    return {"results": results, "total_categories": len(_HOT_CATEGORIES)}


@router.get("/ecom/warmup/status")
async def ecom_warmup_status():
    """查看各品类缓存状态"""
    from netcheck.trace_db import get_ecom_sites, get_conn
    status = []
    for kw in _HOT_CATEGORIES:
        with get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM ecom_sites WHERE category LIKE ?", (f"%{kw}%",)
            ).fetchone()[0]
            with_tt = conn.execute(
                "SELECT COUNT(*) FROM ecom_sites WHERE category LIKE ? AND social LIKE '%\"tiktok\"%'",
                (f"%{kw}%",)
            ).fetchone()[0]
        status.append({"keyword": kw, "total": total, "with_tiktok": with_tt})
    return {"categories": status}


# ── 独立站复刻报告 ────────────────────────────────────────────

@router.get("/ecom/clone-report")
async def ecom_clone_report(domain: str):
    """生成独立站复刻报告：建站方案 + 商品数据 + 营销策略"""
    import aiohttp, ssl as _ssl, re as _re
    from datetime import datetime as _dt
    from netcheck.trace_db import get_ecom_site

    # 先查缓存
    cached = get_ecom_site(domain)

    ssl_ctx = _ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = _ssl.CERT_NONE

    # 并发抓：商品列表 + 分类列表
    async def _get_products(domain):
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                async with s.get(f"https://{domain}/products.json?limit=20",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        d = await r.json(content_type=None)
                        products = d.get("products", [])
                        result = []
                        for p in products[:10]:
                            variants = p.get("variants", [])
                            prices = [float(v.get("price", 0)) for v in variants if v.get("price")]
                            result.append({
                                "title": p.get("title", ""),
                                "type": p.get("product_type", ""),
                                "tags": p.get("tags", "")[:80],
                                "min_price": min(prices) if prices else 0,
                                "max_price": max(prices) if prices else 0,
                                "variants": len(variants),
                                "image": (p.get("images") or [{}])[0].get("src", "")[:100],
                            })
                        return result
        except Exception:
            pass
        return []

    async def _get_collections(domain):
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
                async with s.get(f"https://{domain}/collections.json?limit=20",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 200:
                        d = await r.json(content_type=None)
                        return [c.get("title", "") for c in d.get("collections", [])[:10]]
        except Exception:
            pass
        return []

    import asyncio as _asyncio
    products, collections = await _asyncio.gather(
        _get_products(domain), _get_collections(domain)
    )

    # 价格分析
    prices = [p["min_price"] for p in products if p["min_price"] > 0]
    price_range = ""
    if prices:
        price_range = f"${min(prices):.0f} - ${max(prices):.0f}"
        avg_price = sum(prices) / len(prices)
        main_price = f"${avg_price:.0f}"
    else:
        main_price = cached.get("price_hint", "未知")

    # 从缓存或已有数据组装报告
    platform = cached.get("platform", "未知")
    cdn = cached.get("cdn", "未知")
    apps = cached.get("shopify_apps", [])
    social = cached.get("social", {})
    payment = cached.get("payment", [])
    tech = cached.get("tech_stack", [])
    product_count = cached.get("product_count", len(products))
    reg_date = cached.get("registered_at", "")

    # 推断流量来源
    traffic_sources = []
    if social.get("tiktok"): traffic_sources.append(f"TikTok内容 ({social['tiktok']})")
    if social.get("tt_pixel_id"): traffic_sources.append("TikTok广告投放")
    if social.get("fb_pixel_id"): traffic_sources.append(f"Facebook广告 (Pixel: {social['fb_pixel_id']})")
    if "Google Analytics" in tech: traffic_sources.append("Google SEO/广告")
    if social.get("instagram"): traffic_sources.append(f"Instagram ({social['instagram']})")
    if not traffic_sources: traffic_sources.append("流量来源未知")

    # 推断运营成熟度
    maturity_score = 0
    maturity_items = []
    if apps: maturity_score += len(apps) * 10; maturity_items.append(f"已安装 {len(apps)} 个 Shopify App")
    if "Klaviyo" in tech: maturity_score += 20; maturity_items.append("邮件营销（Klaviyo）")
    if social.get("tiktok"): maturity_score += 15; maturity_items.append("TikTok 社媒运营")
    if social.get("fb_pixel_id"): maturity_score += 20; maturity_items.append("Facebook 广告投放")
    if product_count > 100: maturity_score += 15; maturity_items.append(f"商品丰富（{product_count}+ SKU）")
    if reg_date and reg_date < "2023": maturity_score += 20; maturity_items.append("老店（3年以上）")
    maturity_level = "成熟" if maturity_score >= 60 else "中等" if maturity_score >= 30 else "初级"

    # 外部链接
    links = {
        "store": f"https://{domain}",
        "products_api": f"https://{domain}/products.json",
        "fb_ad_library": f"https://www.facebook.com/ads/library/?q={domain.split('.')[0]}&search_type=keyword_unordered",
    }
    if social.get("tiktok"):
        handle = social["tiktok"].lstrip("@")
        links["tiktok"] = f"https://www.tiktok.com/@{handle}"
    if social.get("instagram"):
        handle = social["instagram"].lstrip("@")
        links["instagram"] = f"https://www.instagram.com/{handle}"

    return {
        "domain": domain,
        "generated_at": _dt.now().isoformat()[:16],
        # 建站方案
        "tech_setup": {
            "platform": platform,
            "cdn": cdn,
            "apps": apps,
            "payment": payment,
            "recommended_stack": f"{platform} + {' + '.join(apps[:3]) if apps else '基础配置'}",
        },
        # 商品数据
        "products": {
            "total_count": product_count,
            "price_range": price_range or main_price,
            "collections": collections,
            "top_products": products[:5],
        },
        # 营销策略
        "marketing": {
            "traffic_sources": traffic_sources,
            "social": social,
            "maturity_level": maturity_level,
            "maturity_score": min(maturity_score, 100),
            "maturity_items": maturity_items,
        },
        # 外部链接
        "links": links,
    }
