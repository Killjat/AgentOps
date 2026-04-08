"""AI 分析网络检测结果"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from netcheck.models import CheckTask, NodeResult, IPType, PathQuality


async def ai_analyze_node(result: NodeResult, target: str) -> NodeResult:
    """用 AI 分析单个节点的检测结果"""
    from llm import chat

    prompt = f"""你是一个网络质量分析专家，专注于跨境电商 IP 质量检测。

检测目标：{target}
节点：{result.agent_id}

出口 IP 信息：
- IP: {result.exit_ip}
- 位置: {result.ip_city}, {result.ip_region}, {result.ip_country}
- ASN/组织: {result.ip_org}
- IP 类型判断: {result.ip_type}

路由路径（{len(result.traceroute_hops)} 跳）：
{' → '.join(result.traceroute_hops[:15]) if result.traceroute_hops else '无数据'}

访问延迟：{result.latency_ms}ms

请分析：
1. 这个 IP 是住宅 IP、机房 IP 还是代理 IP？判断依据是什么？
2. 路由路径是否干净？有无可疑的代理节点？
3. 对于 TikTok 跨境电商使用，这个 IP 质量如何？风险等级？
4. 一句话建议

请用简洁的中文回答，重点突出风险点。"""

    try:
        analysis = await chat([{"role": "user", "content": prompt}], max_tokens=300)
        result.analysis = analysis

        # 提取建议（最后一句）
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
