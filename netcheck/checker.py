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
    "traceroute_android": "traceroute -n -m 20 {target}",  # AndroidNetTools 会拦截并用原生实现
    "latency": "curl -s -o /dev/null -w '%{{time_total}}' --max-time 10 https://{target}",
    "latency_windows": "powershell -Command \"(Measure-Command {{ Invoke-WebRequest -Uri 'https://{target}' -UseBasicParsing }}).TotalMilliseconds\"",
    "latency_android": "curl -s -o /dev/null -w '%{{time_total}}' --max-time 10 https://{target}",  # OkHttp 拦截
    # TikTok 封禁检测
    "tiktok_check": "curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 -H 'User-Agent: Mozilla/5.0' 'https://www.tiktok.com/api/recommend/item_list/?count=1'",
    # DNS 归属地检测
    "dns_check": "curl -s --max-time 5 https://dns.google/resolve?name={target}&type=A | head -c 500",
    # Android DNS 解析（AndroidNetTools 拦截 nslookup）
    "dns_android": "nslookup {target}",
}

# 已知机房 ASN 关键词（黑名单）
DC_KEYWORDS = [
    "amazon", "aws", "google", "microsoft", "azure", "alibaba", "aliyun",
    "tencent", "huawei", "vultr", "linode", "digitalocean", "hetzner",
    "ovh", "choopa", "quadranet", "psychz", "hostwinds", "buyvm",
    "datacamp", "m247", "serverius", "combahton", "frantech",
    "arosscloud", "clouvider", "tzulo", "sharktech", "reliablesite",
    "cognetcloud", "cognet", "it7 networks", "it7net",
    "as14061", "as16509", "as15169", "as8075", "as45090", "as400619", "as401701",
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


async def detect_proxy_type(exec_cmd, is_windows: bool) -> dict:
    """检测代理类型：通过端口、接口、环境变量推断"""
    result = {"type": "unknown", "detail": ""}

    if is_windows:
        # Windows：检查常见代理端口
        ports_raw = await exec_cmd("netstat -ano | findstr LISTENING | findstr -E '1080|7890|10808|8080|1087'", 8)
        if "7890" in ports_raw:
            return {"type": "clash_system", "detail": "检测到 Clash 系统代理端口 7890"}
        if "1080" in ports_raw:
            return {"type": "socks5", "detail": "检测到 SOCKS5 代理端口 1080"}
        if "10808" in ports_raw:
            return {"type": "v2ray", "detail": "检测到 V2Ray 代理端口 10808"}
        # 检查系统代理设置
        proxy_raw = await exec_cmd('reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings" /v ProxyEnable', 8)
        if "0x1" in proxy_raw:
            return {"type": "system_proxy", "detail": "Windows 系统代理已启用"}
        return {"type": "none", "detail": "未检测到代理"}

    # Linux/macOS
    # 1. 检查 TUN 接口（TUN 模式代理）
    iface_raw = await exec_cmd("ip addr 2>/dev/null || ifconfig 2>/dev/null", 8)
    if any(x in iface_raw for x in ["tun0", "utun", "wg0", "tap0"]):
        if "wg0" in iface_raw:
            return {"type": "wireguard", "detail": "检测到 WireGuard VPN 接口 wg0"}
        if any(x in iface_raw for x in ["tun0", "utun"]):
            return {"type": "tun_mode", "detail": "检测到 TUN 模式代理（Clash TUN/V2Ray TUN）"}

    # 2. 检查代理端口
    ports_raw = await exec_cmd("ss -tlnp 2>/dev/null | grep -E '1080|7890|10808|8080|1087' || netstat -tlnp 2>/dev/null | grep -E '1080|7890|10808'", 8)
    if "7890" in ports_raw:
        return {"type": "clash_system", "detail": "检测到 Clash 系统代理端口 7890"}
    if "10808" in ports_raw:
        return {"type": "v2ray", "detail": "检测到 V2Ray 代理端口 10808"}
    if "1080" in ports_raw:
        return {"type": "socks5", "detail": "检测到 SOCKS5 代理端口 1080"}

    # 3. 检查环境变量
    env_raw = await exec_cmd("echo $http_proxy $https_proxy $ALL_PROXY", 5)
    if env_raw.strip() and env_raw.strip() != "  ":
        return {"type": "env_proxy", "detail": f"检测到代理环境变量: {env_raw.strip()[:60]}"}

    return {"type": "none", "detail": "未检测到本地代理进程"}


def get_fix_advice(proxy_type: str, leaked: bool) -> str:
    """根据代理类型和泄露状态给出精准修复建议"""
    if not leaked:
        return "✅ DNS 配置正常，无需修改"

    advice = {
        "clash_system": (
            "🔧 修复方法（Clash 系统代理）：\n"
            "打开 Clash → 设置 → DNS → 开启「DNS 劫持」\n"
            "或在 config.yaml 中设置 dns.enhanced-mode: fake-ip"
        ),
        "tun_mode": (
            "🔧 修复方法（TUN 模式）：\n"
            "TUN 模式通常会自动接管 DNS，请检查代理软件的 DNS 设置\n"
            "确认「DNS 劫持」或「接管系统 DNS」已开启"
        ),
        "v2ray": (
            "🔧 修复方法（V2Ray）：\n"
            "在 V2Ray 配置中添加 DNS 路由规则，将 DNS 查询路由到代理\n"
            "或开启 Fake DNS 模式"
        ),
        "wireguard": (
            "🔧 修复方法（WireGuard）：\n"
            "在 WireGuard 配置中设置 DNS = 8.8.8.8\n"
            "确保 AllowedIPs 包含 0.0.0.0/0（全局模式）"
        ),
        "socks5": (
            "🔧 修复方法（SOCKS5 代理）：\n"
            "手动将系统 DNS 改为 8.8.8.8 或 1.1.1.1\n"
            "或使用支持 DNS 代理的客户端（如 Proxifier）"
        ),
        "system_proxy": (
            "🔧 修复方法（系统代理）：\n"
            "系统代理不会接管 DNS，建议改用 TUN 模式\n"
            "或手动将 DNS 服务器改为 8.8.8.8"
        ),
        "none": (
            "🔧 修复方法：\n"
            "未检测到代理进程，可能是路由器翻墙\n"
            "在路由器设置中将 DNS 改为 8.8.8.8，并确保 DNS 查询走代理"
        ),
    }
    return advice.get(proxy_type, (
        "🔧 修复建议：\n"
        "将系统 DNS 改为 8.8.8.8 或 1.1.1.1\n"
        "并确保代理软件开启了 DNS 劫持功能"
    ))


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


async def multi_source_ip_check(ip: str) -> dict:
    """
    多数据源 IP 定位对比（参考 IPPure）
    查询 ipinfo.io / ip-api.com / db-ip.com，对比定位一致性
    定位不一致 → 风险信号（可能是广播 IP 或数据库错误）
    同时获取 privacy 字段（vpn/proxy/tor/hosting 标记）
    """
    import aiohttp, ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    sources = {}

    async def query(name: str, url: str, parse_fn):
        try:
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        sources[name] = parse_fn(data)
        except Exception:
            pass

    await asyncio.gather(
        query("ipinfo", f"https://ipinfo.io/{ip}/json", lambda d: {
            "country": d.get("country", ""),
            "city": d.get("city", ""),
            "org": d.get("org", ""),
            "hosting": d.get("privacy", {}).get("hosting", False) if isinstance(d.get("privacy"), dict) else False,
            "vpn": d.get("privacy", {}).get("vpn", False) if isinstance(d.get("privacy"), dict) else False,
            "proxy": d.get("privacy", {}).get("proxy", False) if isinstance(d.get("privacy"), dict) else False,
        }),
        query("ip-api", f"http://ip-api.com/json/{ip}?fields=country,city,org,proxy,hosting,mobile", lambda d: {
            "country": d.get("countryCode", ""),
            "city": d.get("city", ""),
            "org": d.get("org", ""),
            "hosting": d.get("hosting", False),
            "vpn": d.get("proxy", False),
            "proxy": d.get("proxy", False),
        }),
        query("db-ip", f"https://api.db-ip.com/v2/free/{ip}", lambda d: {
            "country": d.get("countryCode", ""),
            "city": d.get("city", ""),
            "org": "",
            "hosting": False,
            "vpn": False,
            "proxy": False,
        }),
    )

    # 定位一致性分析
    countries = [v["country"] for v in sources.values() if v.get("country")]
    country_consistent = len(set(countries)) <= 1

    # 任意数据源标记为 hosting/vpn/proxy 即触发
    is_hosting = any(v.get("hosting") for v in sources.values())
    is_vpn = any(v.get("vpn") for v in sources.values())
    is_proxy = any(v.get("proxy") for v in sources.values())

    return {
        "sources": sources,
        "country_consistent": country_consistent,
        "countries": countries,
        "is_hosting": is_hosting,
        "is_vpn": is_vpn,
        "is_proxy": is_proxy,
    }


async def check_outbound_split(exec_cmd, is_windows: bool) -> dict:
    """
    出口分流检测（参考 IPPure 出口检测）
    检测访问国内/国际/AI 网站时走的出口 IP 是否一致
    不一致 → 分流代理，TikTok 可能识别为异常
    """
    # 用不同目标检测出口 IP
    targets = {
        "国内(baidu)": "https://www.baidu.com/s?wd=ip" if not is_windows else None,
        "国际(cloudflare)": "https://cloudflare.com/cdn-cgi/trace",
        "TikTok": "https://www.tiktok.com/",
    }

    async def get_exit_ip(label: str, url: str) -> tuple:
        if not url:
            return label, None
        try:
            # 通过 ipinfo 检测当前出口
            raw = await exec_cmd(
                f"curl -s --max-time 6 https://ipinfo.io/json",
                8
            )
            import json
            d = json.loads(raw)
            return label, d.get("ip", "")
        except Exception:
            return label, None

    # 只检测 cloudflare trace（最可靠，不依赖 ipinfo）
    async def get_cf_ip() -> str:
        try:
            raw = await exec_cmd("curl -s --max-time 6 https://cloudflare.com/cdn-cgi/trace", 8)
            for line in raw.splitlines():
                if line.startswith("ip="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return ""

    async def get_tiktok_ip() -> str:
        """通过访问 TikTok 时的出口 IP（用 curl -v 看 connect）"""
        try:
            raw = await exec_cmd(
                "curl -s --max-time 6 -w '%{remote_ip}' -o /dev/null https://www.tiktok.com/",
                8
            )
            return raw.strip()
        except Exception:
            return ""

    cf_ip, tk_ip = await asyncio.gather(get_cf_ip(), get_tiktok_ip())

    # 主出口 IP（ipinfo）
    main_ip = ""
    try:
        raw = await exec_cmd("curl -s --max-time 6 https://ipinfo.io/ip", 8)
        main_ip = raw.strip()
    except Exception:
        pass

    split_detected = bool(cf_ip and tk_ip and cf_ip != tk_ip)
    return {
        "main_ip": main_ip,
        "cloudflare_ip": cf_ip,
        "tiktok_ip": tk_ip,
        "split_detected": split_detected,
    }


def calc_purity_score(
    ip_type: IPType,
    risk_flags: list,
    tiktok_blocked: bool,
    dns_leaked: bool,
    multi_source: dict,
    latency_ms: float,
    country: str,
) -> int:
    """
    计算 IP 纯净度评分（0-100，越高越纯净）
    参考 IPPure 系数逻辑
    """
    score = 100

    # IP 类型扣分
    if ip_type == IPType.DATACENTER:
        score -= 40
    elif ip_type == IPType.PROXY:
        score -= 50

    # TikTok 封禁
    if tiktok_blocked:
        score -= 30

    # DNS 泄露
    if dns_leaked:
        score -= 15

    # 多数据源标记
    if multi_source.get("is_hosting"):
        score -= 20
    if multi_source.get("is_vpn") or multi_source.get("is_proxy"):
        score -= 25

    # 定位不一致（数据库打架）
    if not multi_source.get("country_consistent", True):
        score -= 10

    # 风险标签
    for flag in risk_flags:
        if "隧道" in flag:
            score -= 20
        elif "机房" in flag:
            score -= 15
        elif "路径过长" in flag:
            score -= 10
        elif "延迟异常" in flag:
            score -= 10

    # 延迟地理校验
    if latency_ms > 0 and country in LATENCY_EXPECT:
        _, max_ms = LATENCY_EXPECT[country]
        if latency_ms > max_ms * 2:
            score -= 15

    return max(0, min(100, score))


async def check_node(agent_id: str, target: str, os_type: str = "linux") -> NodeResult:
    """在单个 agent 上执行完整检测"""
    from routers.agents import _ws_call
    result = NodeResult(agent_id=agent_id, status="running")

    is_windows = "windows" in os_type.lower()
    is_android = "android" in os_type.lower()

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

        # 2. traceroute（Android 用原生实现，服务端解析输出格式相同）
        if is_android:
            tr_cmd = CMDS["traceroute_android"].format(target=target)
        elif is_windows:
            tr_cmd = CMDS["traceroute_windows"].format(target=target)
        else:
            tr_cmd = CMDS["traceroute_linux"].format(target=target)
        tr_raw = await exec_cmd(tr_cmd, timeout=50)
        result.traceroute_raw = tr_raw[:3000]
        result.traceroute_hops = parse_traceroute(tr_raw)
        result.traceroute_enriched = await enrich_hops(result.traceroute_hops)

        # 3. 延迟
        if is_android:
            lat_cmd = CMDS["latency_android"].format(target=target)
        elif is_windows:
            lat_cmd = CMDS["latency_windows"].format(target=target)
        else:
            lat_cmd = CMDS["latency"].format(target=target)
        lat_raw = await exec_cmd(lat_cmd, timeout=15)
        try:
            result.latency_ms = round(float(lat_raw.strip()) * (1 if is_windows else 1000), 1)
        except Exception:
            pass

        # 4. TikTok 封禁检测
        if "tiktok" in target.lower():
            tk_raw = await exec_cmd(CMDS["tiktok_check"], timeout=15)
            result.tiktok_status_code = tk_raw.strip()[:10] if tk_raw else ""

        # 5. DNS 泄露检测
        if is_android:
            # Android：直接用 nslookup（AndroidNetTools 拦截，返回 Java InetAddress 结果）
            dns_raw = await exec_cmd(CMDS["dns_android"].format(target=target), timeout=10)
            import re as _re
            local_ips = _re.findall(r'Address:\s*(\d+\.\d+\.\d+\.\d+)', dns_raw)
        else:
            dns_cmd = f"nslookup {target} 2>/dev/null | grep -A1 'Name:' | grep 'Address' | awk '{{print $2}}' | head -3"
            dns_raw = await exec_cmd(dns_cmd, timeout=10)
            if not dns_raw.strip():
                dns_raw = await exec_cmd(f"ping -c 1 -W 2 {target} 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+' | head -1", timeout=8)
            local_ips = [ip.strip() for ip in dns_raw.strip().splitlines() if ip.strip() and '.' in ip]

        dns_leak = await check_dns_leak(target, local_ips)

        # 6. 代理类型检测（Android 跳过 shell 端口检测）
        if not is_android:
            proxy_info = await detect_proxy_type(exec_cmd, is_windows)
        else:
            proxy_info = {"type": "unknown", "detail": "Android 不支持端口检测"}
        dns_leak["proxy_type"] = proxy_info.get("type", "unknown")
        dns_leak["proxy_detail"] = proxy_info.get("detail", "")
        dns_leak["fix_advice"] = get_fix_advice(proxy_info.get("type", "unknown"), dns_leak.get("leaked", False))
        result.dns_leak = dns_leak

        # 7. 路径质量分析
        result.path_quality, result.risk_score, flags = analyze_path(
            result.traceroute_hops, result.ip_org,
            result.ip_country, result.latency_ms
        )
        result.risk_flags = flags

        # 8. 多数据源 IP 定位对比（参考 IPPure）
        if result.exit_ip:
            result.multi_source = await multi_source_ip_check(result.exit_ip)
            # 多数据源标记为机房/VPN → 追加风险标签
            if result.multi_source.get("is_hosting") and "🏢 机房IP" not in result.risk_flags:
                result.risk_flags.append("🏢 多源确认：机房托管IP")
            if result.multi_source.get("is_vpn") or result.multi_source.get("is_proxy"):
                result.risk_flags.append("🔀 多源确认：VPN/代理IP")
            if not result.multi_source.get("country_consistent", True):
                result.risk_flags.append("⚠️ 定位不一致：多数据库地理位置冲突")

        # 9. 出口分流检测（Android 也支持，AndroidNetTools 拦截 curl cloudflare）
        if not is_windows:
            result.outbound_split = await check_outbound_split(exec_cmd, is_windows)
            if result.outbound_split.get("split_detected"):
                result.risk_flags.append("🔀 出口分流：TikTok与全局走不同IP")

        # 10. 综合纯净度评分
        result.purity_score = calc_purity_score(
            result.ip_type,
            result.risk_flags,
            result.tiktok_blocked,
            dns_leak.get("leaked", False),
            result.multi_source,
            result.latency_ms,
            result.ip_country,
        )

        result.status = "success"

    except Exception as e:
        result.error = str(e)
        result.status = "failed"

    return result
