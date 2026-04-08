"""AI 分析网络检测结果"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from netcheck.models import CheckTask, NodeResult, IPType, PathQuality


async def ai_analyze_node(result: NodeResult, target: str) -> NodeResult:
    """用 AI 分析单个节点的检测结果"""
    from llm import chat

    # TikTok 封禁判断
    if result.tiktok_status_code in ("403", "451", "000"):
        result.tiktok_blocked = True

    flags_str = "\n".join(f"  - {f}" for f in result.risk_flags) if result.risk_flags else "  无"

    tiktok_str = ""
    if result.tiktok_status_code:
        blocked = result.tiktok_status_code in ("403", "451", "000")
        tiktok_str = f"\nTikTok 访问状态码：{result.tiktok_status_code}（{'🚫 已封禁' if blocked else '✅ 可访问'}）"

    prompt = f"""你是一个专注于跨境电商 IP 质量检测的网络专家。

检测目标：{target}
节点：{result.agent_id}

出口 IP：{result.exit_ip}
位置：{result.ip_city}, {result.ip_region}, {result.ip_country}
ASN/运营商：{result.ip_org}
IP 类型：{result.ip_type}
访问延迟：{result.latency_ms}ms{tiktok_str}

路由路径（{len(result.traceroute_hops)} 跳）：
{' → '.join(result.traceroute_hops[:15]) if result.traceroute_hops else '无数据'}

自动检测到的风险标签：
{flags_str}

风险评分：{result.risk_score}/100

请用简洁中文分析：
1. 综合判断：这个 IP 适合用于 TikTok 跨境电商吗？
2. 主要风险点是什么？
3. 一句话建议

不超过150字。"""

    try:
        analysis = await chat([{"role": "user", "content": prompt}], max_tokens=250)
        result.analysis = analysis
        lines = [l.strip() for l in analysis.splitlines() if l.strip()]
        if lines:
            result.recommendation = lines[-1]
    except Exception as e:
        result.analysis = f"AI 分析失败: {e}"

    return result


async def ai_summary(task: CheckTask) -> str:
    """生成整体检测报告"""
    from llm import chat

    nodes_desc = []
    for r in task.results:
        if r.status == "success":
            nodes_desc.append(
                f"- {r.agent_id} ({r.ip_country} {r.ip_city}): "
                f"IP={r.exit_ip}, 类型={r.ip_type}, "
                f"延迟={r.latency_ms}ms, 风险={r.risk_score}/100\n"
                f"  {r.analysis[:100] if r.analysis else ''}"
            )

    prompt = f"""你是跨境电商 IP 质量专家。

检测目标：{task.target}
检测节点数：{len(task.results)}

各节点结果：
{chr(10).join(nodes_desc)}

请生成一份简洁的检测报告：
1. 哪些节点 IP 质量好（适合 TikTok 跨境电商）
2. 哪些节点有风险（机房 IP、代理特征）
3. 整体建议

用表格或列表形式，简洁清晰。"""

    try:
        return await chat([{"role": "user", "content": prompt}], max_tokens=500)
    except Exception as e:
        return f"报告生成失败: {e}"
