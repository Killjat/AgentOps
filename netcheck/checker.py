"""网络检测核心逻辑 - 在各 agent 上执行检测命令"""
import asyncio
import logging
import re
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from netcheck.models import NodeResult, IPType, PathQuality

logger = logging.getLogger(__name__)

# 各平台的检测命令
CMDS = {
    "ipinfo": "curl -s --max-time 8 https://ipinfo.io/json",
    "traceroute_linux": "traceroute -n -m 20 -w 2 {target} 2>/dev/null || tracepath -n {target} 2>/dev/null",
    "traceroute_windows": "tracert -d -h 20 -w 2000 {target}",
    "latency": "curl -s -o /dev/null -w '%{{time_total}}' --max-time 10 https://{target}",
    "latency_windows": "powershell -Command \"(Measure-Command {{ Invoke-WebRequest -Uri 'https://{target}' -UseBasicParsing }}).TotalMilliseconds\"",
    # TikTok 封禁检测
    "tiktok_check": "curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 -H 'User-Agent: Mozilla/5.0' 'https://www.tiktok.com/api/recommend/item_list/?count=1'",
    # DNS 归属地检测
    "dns_check": "curl -s --max-time 5 https://dns.google/resolve?name={target}&type=A | head -c 500",
}

# 已知机房 ASN 关键词（黑名单）
DC_KEYWORDS = [
    "amazon", "aws", "google", "microsoft", "azure", "alibaba", "aliyun",
    "tencent", "huawei", "vultr", "linode", "digitalocean", "hetzner",
    "ovh", "choopa", "quadranet", "psychz", "hostwinds", "buyvm",
    "datacamp", "m247", "serverius", "combahton", "frantech",
    "arosscloud", "clouvider", "tzulo", "sharktech", "reliablesite",
    "as14061", "as16509", "as15169", "as8075", "as45090", "as400619",
]

RESIDENTIAL_KEYWORDS = [
    "comcast", "at&t", "verizon", "spectrum", "cox", "charter",
    "china telecom", "china unicom", "china mobile",
    "softbank", "ntt", "kddi", "docomo",
    "bt ", "sky ", "virgin", "talktalk",
    "residential", "broadband", "dsl", "fiber",
]

PROXY_KEYWORDS = [
    "cloudflare", "fastly", "akamai", "incapsula",
    "vpn", "proxy", "tunnel", "tor ",
]

# 内网 IP 段
PRIVATE_RANGES = [
    re.compile(r'^10\.'),
    re.compile(r'^192\.168\.'),
    re.compile(r'^172\.(1[6-9]|2[0-9]|3[01])\.'),
    re.compile(r'^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.'),  # CGNAT
]

# 延迟地理预期（ms）
LATENCY_EXPECT = {
    "US": (80, 250),   # 中国到美国
    "JP": (50, 150),   # 中国到日本
    "SG": (30, 100),   # 中国到新加坡
    "GB": (150, 350),  # 中国到英国
    "DE": (150, 350),  # 中国到德国
    "HK": (10, 50),    # 中国到香港
    "KR": (30, 100),   # 中国到韩国
}


def is_private_ip(ip: str) -> bool:
    return any(p.match(ip) for p in PRIVATE_RANGES)


def classify_ip(org: str) -> IPType:
    org_lower = org.lower()
    for kw in PROXY_KEYWORDS:
        if kw in org_lower:
            return IPType.PROXY
    for kw in DC_KEYWORDS:
        if kw in org_lower:
            return IPType.DATACENTER
    for kw in RESIDENTIAL_KEYWORDS:
        if kw in org_lower:
            return IPType.RESIDENTIAL
    return IPType.UNKNOWN


def parse_traceroute(raw: str) -> list:
    hops = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("traceroute") or line.startswith("Tracing"):
            continue
        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', line)
        if ips:
            hops.append(ips[0])
        elif "* * *" in line or "***" in line:
            hops.append("*")
    return hops


async def enrich_hops(hops: list) -> list:
    """
    对 traceroute 每跳 IP 查询地理位置和 ASN，返回带标注的跳点列表
    格式: [{"ip": "1.2.3.4", "country": "US", "city": "LA", "org": "AS xxx", "tag": "机房"}]
    """
    import aiohttp, ssl

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    result = []
    # 过滤掉内网 IP 和星号，只查公网 IP，最多查 15 个
    public_ips = [h for h in hops if h != "*" and not is_private_ip(h)][:15]

    if not public_ips:
        return [{"ip": h, "country": "", "city": "", "org": "", "tag": _tag_hop(h)} for h in hops]

    # 批量查询（ipinfo 支持批量，但免费版限制，改为并发单查）
    async def query_ip(ip: str) -> dict:
        try:
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as s:
                async with s.get(f"https://ipinfo.io/{ip}/json", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        d = await r.json()
                        return {
                            "ip": ip,
                            "country": d.get("country", ""),
                            "city": d.get("city", ""),
                            "org": d.get("org", ""),
                            "tag": _tag_hop_org(d.get("org", ""), ip)
                        }
        except Exception:
            pass
        return {"ip": ip, "country": "", "city": "", "org": "", "tag": _tag_hop(ip)}

    # 并发查询所有公网 IP
    ip_info_map = {}
    tasks = [query_ip(ip) for ip in public_ips]
    results = await asyncio.gather(*tasks)
    for r in results:
        ip_info_map[r["ip"]] = r

    # 按原始顺序组装
    for hop in hops:
        if hop == "*":
            result.append({"ip": "*", "country": "", "city": "", "org": "", "tag": "超时"})
        elif is_private_ip(hop):
            result.append({"ip": hop, "country": "内网", "city": "", "org": "", "tag": _tag_hop(hop)})
        else:
            result.append(ip_info_map.get(hop, {"ip": hop, "country": "", "city": "", "org": "", "tag": ""}))

    return result


def _tag_hop(ip: str) -> str:
    """对单个 IP 打标签"""
    if ip == "*":
        return "超时"
    if is_private_ip(ip):
        if ip.startswith("10."):
            return "🔒 内网(10.x)"
        elif ip.startswith("192.168."):
            return "🔒 内网(192.168.x)"
        elif ip.startswith("172."):
            return "🔒 内网(172.x)"
        else:
            return "🔒 CGNAT"
    return ""


def _tag_hop_org(org: str, ip: str) -> str:
    """根据 ASN/组织名给跳点打标签"""
    if not org:
        return _tag_hop(ip)
    org_lower = org.lower()
    ip_type = classify_ip(org)
    if ip_type == IPType.DATACENTER:
        return "🏢 机房"
    elif ip_type == IPType.PROXY:
        return "🔀 代理"
    elif ip_type == IPType.RESIDENTIAL:
        return "🏠 住宅"
    # 骨干网/运营商
    backbone_keywords = ["transit", "backbone", "tier", "cogent", "level3", "telia", "ntt", "hurricane"]
    if any(k in org_lower for k in backbone_keywords):
        return "🌐 骨干网"
    return "📡 ISP"


def analyze_path(hops: list, org: str, country: str, latency_ms: float) -> tuple:
    """
    分析路径质量，返回 (PathQuality, risk_score, flags)
    flags: 检测到的风险标签列表
    """
    if not hops:
        return PathQuality.SUSPECT, 50, ["无路由数据"]

    flags = []
    risk = 0

    # 1. IP 类型风险
    ip_type = classify_ip(org)
    if ip_type == IPType.DATACENTER:
        risk += 40
        flags.append("🏢 机房IP")
    elif ip_type == IPType.PROXY:
        risk += 60
        flags.append("🔀 代理/VPN")

    # 2. 隧道代理检测：链路中出现连续内网 IP
    private_count = sum(1 for h in hops if h != "*" and is_private_ip(h))
    if private_count >= 3:
        risk += 30
        flags.append(f"🕳️ 隧道代理（{private_count}个内网跳）")
    elif private_count >= 1:
        risk += 10
        flags.append(f"⚠️ NAT转发（{private_count}个内网跳）")

    # 3. 路径过长
    real_hops = [h for h in hops if h != "*"]
    if len(real_hops) > 15:
        risk += 20
        flags.append(f"📏 路径过长（{len(real_hops)}跳）")

    # 4. 大量超时跳
    star_count = hops.count("*")
    if star_count / max(len(hops), 1) > 0.4:
        risk += 15
        flags.append(f"⏱️ 大量超时跳（{star_count}个）")

    # 5. 延迟地理校验
    if latency_ms > 0 and country in LATENCY_EXPECT:
        min_ms, max_ms = LATENCY_EXPECT[country]
        if latency_ms > max_ms * 2:
            risk += 25
            flags.append(f"🐌 延迟异常高（{latency_ms}ms，预期{min_ms}-{max_ms}ms）")
        elif latency_ms > max_ms:
            risk += 10
            flags.append(f"⚠️ 延迟偏高（{latency_ms}ms）")

    if risk >= 60:
        return PathQuality.BAD, min(risk, 100), flags
    elif risk >= 30:
        return PathQuality.SUSPECT, risk, flags
    else:
        return PathQuality.CLEAN, risk, flags


async def check_dns_leak(target: str, local_ips: list = None) -> dict:
    """DNS 泄露检测：对比 agent 的 DNS 解析结果和 Google DoH"""
    result = {"local_ips": local_ips or [], "google_ips": [], "leaked": False, "detail": ""}
    try:
        import aiohttp, ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(
                f"https://dns.google/resolve?name={target}&type=A",
                headers={"Accept": "application/dns-json"},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    for ans in data.get("Answer", []):
                        if ans.get("type") == 1:
                            result["google_ips"].append(ans["data"])

        # 判断泄露：本机 DNS 和 Google DNS 结果完全不重叠
        if result["local_ips"] and result["google_ips"]:
            local_set = set(result["local_ips"])
            google_set = set(result["google_ips"])
            result["leaked"] = len(local_set & google_set) == 0
    except Exception as e:
        result["detail"] = f"Google DoH 查询失败: {str(e)[:50]}"
    return result


async def check_node(agent_id: str, target: str, os_type: str = "linux") -> NodeResult:
    """在单个 agent 上执行完整检测"""
    from routers.agents import _ws_call
    result = NodeResult(agent_id=agent_id, status="running")

    is_windows = "windows" in os_type.lower()

    async def exec_cmd(cmd: str, timeout: int = 30) -> str:
        try:
            resp = await _ws_call(agent_id, {
                "type": "exec",
                "command": cmd,
                "timeout": timeout,
            }, timeout=timeout + 5)
            return resp.get("output", "") or ""
        except Exception as e:
            logger.warning(f"[netcheck] {agent_id} 执行失败: {e}")
            return ""

    try:
        # 1. 出口 IP 信息
        ipinfo_raw = await exec_cmd(CMDS["ipinfo"], timeout=15)
        if ipinfo_raw:
            import json
            try:
                info = json.loads(ipinfo_raw)
                result.exit_ip = info.get("ip", "")
                result.ip_city = info.get("city", "")
                result.ip_region = info.get("region", "")
                result.ip_country = info.get("country", "")
                result.ip_org = info.get("org", "")
                result.ip_type = classify_ip(result.ip_org)
            except Exception:
                result.exit_ip = ipinfo_raw.strip()[:50]

        # 2. traceroute
        tr_cmd = CMDS["traceroute_windows"].format(target=target) if is_windows \
                 else CMDS["traceroute_linux"].format(target=target)
        tr_raw = await exec_cmd(tr_cmd, timeout=50)
        result.traceroute_raw = tr_raw[:3000]
        result.traceroute_hops = parse_traceroute(tr_raw)

        # 对每跳 IP 查询地理位置和打标签（服务器端查询，不占用 agent）
        result.traceroute_enriched = await enrich_hops(result.traceroute_hops)

        # 3. 延迟
        lat_cmd = CMDS["latency_windows"].format(target=target) if is_windows \
                  else CMDS["latency"].format(target=target)
        lat_raw = await exec_cmd(lat_cmd, timeout=15)
        try:
            result.latency_ms = round(float(lat_raw.strip()) * (1 if is_windows else 1000), 1)
        except Exception:
            pass

        # 4. TikTok 封禁检测（仅当目标是 tiktok 相关时）
        if "tiktok" in target.lower():
            tk_raw = await exec_cmd(CMDS["tiktok_check"], timeout=15)
            result.tiktok_status_code = tk_raw.strip()[:10] if tk_raw else ""

        # 5. DNS 泄露检测（agent 端解析 + 服务器端 Google DoH 对比）
        dns_cmd = f"nslookup {target} 2>/dev/null | grep -A1 'Name:' | grep 'Address' | awk '{{print $2}}' | head -3"
        dns_raw = await exec_cmd(dns_cmd, timeout=10)
        if not dns_raw.strip():
            # fallback: 用 getent 或 ping 获取 IP
            dns_raw = await exec_cmd(f"ping -c 1 -W 2 {target} 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+' | head -1", timeout=8)
        local_ips = [ip.strip() for ip in dns_raw.strip().splitlines() if ip.strip() and '.' in ip]

        dns_leak = await check_dns_leak(target, local_ips)
        result.dns_leak = dns_leak

        # 6. 路径质量分析（含新增检测）
        result.path_quality, result.risk_score, flags = analyze_path(
            result.traceroute_hops, result.ip_org,
            result.ip_country, result.latency_ms
        )
        result.risk_flags = flags
        result.status = "success"

    except Exception as e:
        result.error = str(e)
        result.status = "failed"

    return result
