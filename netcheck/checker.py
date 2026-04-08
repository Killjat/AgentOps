"""网络检测核心逻辑 - 在各 agent 上执行检测命令"""
import asyncio
import logging
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
}

# 已知机房 ASN 关键词
DC_KEYWORDS = [
    "amazon", "aws", "google", "microsoft", "azure", "alibaba", "aliyun",
    "tencent", "huawei", "vultr", "linode", "digitalocean", "hetzner",
    "ovh", "choopa", "quadranet", "psychz", "hostwinds", "buyvm",
    "datacamp", "m247", "serverius", "combahton", "frantech",
    "as14061", "as16509", "as15169", "as8075", "as45090",
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


def classify_ip(org: str) -> IPType:
    """根据 ASN/组织名判断 IP 类型"""
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
    """解析 traceroute 输出，提取每跳 IP"""
    hops = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("traceroute") or line.startswith("Tracing"):
            continue
        # 提取 IP 地址
        import re
        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', line)
        if ips:
            hops.append(ips[0])
        elif "* * *" in line or "***" in line:
            hops.append("*")
    return hops


def analyze_path(hops: list, org: str) -> tuple:
    """分析路径质量，返回 (PathQuality, risk_score)"""
    if not hops:
        return PathQuality.SUSPECT, 50

    # 统计星号（超时跳）
    star_count = hops.count("*")
    star_ratio = star_count / max(len(hops), 1)

    risk = 0

    # 机房 IP 风险 +40
    if classify_ip(org) == IPType.DATACENTER:
        risk += 40
    elif classify_ip(org) == IPType.PROXY:
        risk += 60

    # 路径过长（>15跳）风险 +20
    if len(hops) > 15:
        risk += 20

    # 大量超时跳 +20
    if star_ratio > 0.4:
        risk += 20

    if risk >= 60:
        return PathQuality.BAD, min(risk, 100)
    elif risk >= 30:
        return PathQuality.SUSPECT, risk
    else:
        return PathQuality.CLEAN, risk


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
        # 1. 获取出口 IP 信息
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
        tr_raw = await exec_cmd(tr_cmd, timeout=45)
        result.traceroute_raw = tr_raw[:3000]
        result.traceroute_hops = parse_traceroute(tr_raw)

        # 3. 延迟测试
        lat_cmd = CMDS["latency_windows"].format(target=target) if is_windows \
                  else CMDS["latency"].format(target=target)
        lat_raw = await exec_cmd(lat_cmd, timeout=15)
        try:
            result.latency_ms = round(float(lat_raw.strip()) * (1 if is_windows else 1000), 1)
        except Exception:
            pass

        # 4. 路径质量分析
        result.path_quality, result.risk_score = analyze_path(
            result.traceroute_hops, result.ip_org
        )
        result.status = "success"

    except Exception as e:
        result.error = str(e)
        result.status = "failed"

    return result
