"""
威胁情报聚合模块
接入 AbuseIPDB、VirusTotal，查询 IP 的历史威胁记录
"""
import os
import asyncio
import aiohttp
import ssl
from datetime import datetime

# API Keys - 从环境变量读取
ABUSEIPDB_KEY = os.getenv("ABUSEIPDB_KEY", "")
VIRUSTOTAL_KEY = os.getenv("VIRUSTOTAL_KEY", "")

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


async def query_abuseipdb(ip: str) -> dict:
    """查询 AbuseIPDB 举报记录"""
    if not ABUSEIPDB_KEY:
        return {"available": False, "reason": "未配置 API Key"}
    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={"ipAddress": ip, "maxAgeInDays": 90},
                headers={"Key": ABUSEIPDB_KEY, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status != 200:
                    return {"available": False, "reason": f"HTTP {r.status}"}
                d = (await r.json()).get("data", {})
                return {
                    "available": True,
                    "abuse_score": d.get("abuseConfidenceScore", 0),      # 0-100 滥用置信度
                    "total_reports": d.get("totalReports", 0),             # 总举报次数
                    "distinct_users": d.get("numDistinctUsers", 0),        # 不同举报用户数
                    "last_reported": d.get("lastReportedAt", ""),          # 最后举报时间
                    "country": d.get("countryCode", ""),
                    "isp": d.get("isp", ""),
                    "domain": d.get("domain", ""),
                    "is_tor": d.get("isTor", False),
                    "is_public": d.get("isPublic", True),
                    "usage_type": d.get("usageType", ""),                  # 用途类型
                    "categories": _parse_categories(d.get("reports", [])), # 举报类型汇总
                }
    except Exception as e:
        return {"available": False, "reason": str(e)[:80]}


async def query_virustotal(ip: str) -> dict:
    """查询 VirusTotal 检测结果"""
    if not VIRUSTOTAL_KEY:
        return {"available": False, "reason": "未配置 API Key"}
    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(
                f"https://www.virustotal.com/api/v3/ip_addresses/{ip}",
                headers={"x-apikey": VIRUSTOTAL_KEY},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 404:
                    return {"available": True, "not_found": True, "malicious": 0, "suspicious": 0}
                if r.status != 200:
                    return {"available": False, "reason": f"HTTP {r.status}"}
                d = (await r.json()).get("data", {}).get("attributes", {})
                stats = d.get("last_analysis_stats", {})
                votes = d.get("total_votes", {})
                return {
                    "available": True,
                    "not_found": False,
                    "malicious": stats.get("malicious", 0),       # 标记为恶意的引擎数
                    "suspicious": stats.get("suspicious", 0),     # 标记为可疑的引擎数
                    "harmless": stats.get("harmless", 0),
                    "undetected": stats.get("undetected", 0),
                    "community_votes_malicious": votes.get("malicious", 0),
                    "community_votes_harmless": votes.get("harmless", 0),
                    "reputation": d.get("reputation", 0),         # 社区声誉分（负=差）
                    "tags": d.get("tags", []),                    # 标签如 vpn, proxy 等
                    "network": d.get("network", ""),              # 所属网段
                    "as_owner": d.get("as_owner", ""),
                    "last_analysis_date": _ts(d.get("last_analysis_date")),
                }
    except Exception as e:
        return {"available": False, "reason": str(e)[:80]}


async def get_threat_intel(ip: str) -> dict:
    """并发查询所有情报源，返回聚合结果"""
    abuse, vt = await asyncio.gather(
        query_abuseipdb(ip),
        query_virustotal(ip),
    )

    # 综合风险判断
    risk_level, risk_tags = _assess_risk(abuse, vt)

    return {
        "ip": ip,
        "queried_at": datetime.now().isoformat(),
        "abuseipdb": abuse,
        "virustotal": vt,
        "risk_level": risk_level,   # clean / suspicious / malicious
        "risk_tags": risk_tags,     # 风险标签列表
    }


def _assess_risk(abuse: dict, vt: dict) -> tuple:
    """综合评估风险等级"""
    tags = []
    score = 0

    if abuse.get("available"):
        ab_score = abuse.get("abuse_score", 0)
        reports = abuse.get("total_reports", 0)
        if ab_score >= 80:
            score += 3
            tags.append(f"⚠️ AbuseIPDB 高危（{ab_score}分，{reports}次举报）")
        elif ab_score >= 25:
            score += 2
            tags.append(f"🔶 AbuseIPDB 可疑（{ab_score}分，{reports}次举报）")
        elif reports > 0:
            score += 1
            tags.append(f"📋 AbuseIPDB 有举报记录（{reports}次）")
        if abuse.get("is_tor"):
            score += 2
            tags.append("🧅 Tor 出口节点")
        cats = abuse.get("categories", [])
        if cats:
            tags.append(f"举报类型：{', '.join(cats[:3])}")

    if vt.get("available") and not vt.get("not_found"):
        malicious = vt.get("malicious", 0)
        suspicious = vt.get("suspicious", 0)
        if malicious >= 5:
            score += 3
            tags.append(f"🔴 VirusTotal {malicious} 个引擎标记恶意")
        elif malicious >= 1:
            score += 2
            tags.append(f"🟠 VirusTotal {malicious} 个引擎标记恶意")
        elif suspicious >= 1:
            score += 1
            tags.append(f"🟡 VirusTotal {suspicious} 个引擎标记可疑")
        vt_tags = vt.get("tags", [])
        if "vpn" in vt_tags:
            tags.append("🔒 VPN 节点")
        if "proxy" in vt_tags:
            tags.append("🔀 代理节点")
        rep = vt.get("reputation", 0)
        if rep < -10:
            tags.append(f"👎 社区声誉差（{rep}）")

    if score >= 4:
        return "malicious", tags
    elif score >= 2:
        return "suspicious", tags
    else:
        if not tags:
            tags.append("✅ 无已知威胁记录")
        return "clean", tags


# AbuseIPDB 举报类型映射
_CATEGORY_MAP = {
    3: "欺诈订单", 4: "DDoS攻击", 5: "FTP暴力破解", 6: "Ping扫描",
    7: "端口扫描", 9: "开放代理", 10: "Web垃圾邮件", 11: "邮件垃圾",
    14: "端口扫描", 15: "黑客攻击", 16: "SQL注入", 17: "邮件欺骗",
    18: "暴力破解", 19: "暴力破解", 20: "机器人", 21: "IoT攻击",
    22: "VPN IP", 23: "端口扫描"
}

def _parse_categories(reports: list) -> list:
    cats = set()
    for r in reports[:20]:
        for c in r.get("categories", []):
            if c in _CATEGORY_MAP:
                cats.add(_CATEGORY_MAP[c])
    return list(cats)[:5]

def _ts(unix_ts) -> str:
    if not unix_ts:
        return ""
    try:
        return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d")
    except Exception:
        return ""
