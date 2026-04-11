"""
路径收敛检测
当多个节点 traceroute 同一目标时，检测路径是否在某个跳点收敛到同一链路
收敛 = 代理基础设施特征
"""
import sqlite3
import json
import logging
from datetime import datetime
from netcheck.trace_db import get_conn

logger = logging.getLogger(__name__)


def _ensure_convergence_table():
    with get_conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS convergence_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target          TEXT NOT NULL,
            convergence_ip  TEXT NOT NULL,       -- 收敛点 IP
            convergence_hop INTEGER DEFAULT 0,   -- 收敛发生在第几跳
            convergence_org TEXT DEFAULT '',     -- 收敛点运营商
            convergence_country TEXT DEFAULT '', -- 收敛点国家
            node_count      INTEGER DEFAULT 0,   -- 参与节点数
            nodes           TEXT DEFAULT '',     -- 参与节点列表 JSON
            confidence      REAL DEFAULT 0,      -- 置信度 0-1
            tag             TEXT DEFAULT '',     -- proxy_chain / anycast / normal
            analyzed_at     TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_target ON convergence_results(target);
        CREATE INDEX IF NOT EXISTS idx_conv_ip ON convergence_results(convergence_ip);
        CREATE INDEX IF NOT EXISTS idx_conv_tag ON convergence_results(tag);
        """)


def analyze_target(target: str) -> dict:
    """
    分析某个目标 IP 的多节点路径，检测收敛点
    返回分析结果
    """
    with get_conn() as db:
        c = db.cursor()

        # 获取该目标的所有 traceroute 任务
        tasks = c.execute("""
            SELECT task_id, agent_name, os_type FROM traceroute_tasks
            WHERE target=? ORDER BY created_at DESC
        """, (target,)).fetchall()

        if len(tasks) < 2:
            return {"target": target, "tag": "insufficient_data", "node_count": len(tasks)}

        # 获取每个任务的跳点序列
        node_paths = {}
        for task_id, agent_name, os_type in tasks:
            hops = c.execute("""
                SELECT hop_index, ip, org, country, city, asn
                FROM traceroute_hops
                WHERE task_id=? AND is_private=0 AND ip != '*'
                ORDER BY hop_index
            """, (task_id,)).fetchall()
            if hops:
                node_paths[agent_name or task_id] = hops

        if len(node_paths) < 2:
            return {"target": target, "tag": "insufficient_data", "node_count": len(node_paths)}

        # 检测收敛：找到所有节点路径中第一个共同出现的 IP
        convergence_ip, convergence_hop, convergence_info = _find_convergence(node_paths)

        node_count = len(node_paths)
        nodes = list(node_paths.keys())

        if convergence_ip:
            # 计算置信度：参与节点越多、收敛越早，置信度越高
            confidence = min(1.0, (node_count / 3) * (1 - convergence_hop / 20))

            # 判断类型
            if convergence_hop <= 3:
                tag = "proxy_chain"  # 很早就收敛，典型代理链路
            elif convergence_hop <= 6:
                tag = "shared_upstream"  # 共享上游
            else:
                tag = "anycast"  # 较晚收敛，可能是 Anycast

            result = {
                "target": target,
                "convergence_ip": convergence_ip,
                "convergence_hop": convergence_hop,
                "convergence_org": convergence_info.get("org", ""),
                "convergence_country": convergence_info.get("country", ""),
                "node_count": node_count,
                "nodes": nodes,
                "confidence": round(confidence, 2),
                "tag": tag,
            }
        else:
            result = {
                "target": target,
                "convergence_ip": "",
                "convergence_hop": 0,
                "convergence_org": "",
                "convergence_country": "",
                "node_count": node_count,
                "nodes": nodes,
                "confidence": 0,
                "tag": "diverse_paths",  # 路径多样，无收敛
            }

        # 写入数据库
        _save_result(result)
        return result


def _find_convergence(node_paths: dict):
    """
    找到多条路径中第一个共同出现的 IP（收敛点）
    返回 (convergence_ip, hop_index, info_dict)
    """
    # 构建每个节点的 IP 集合（按跳点顺序）
    path_lists = list(node_paths.values())

    # 从第1跳开始，找第一个在所有路径中都出现的 IP
    # 用滑动窗口：对每条路径的每个跳点，检查该 IP 是否在其他所有路径中也出现
    all_ips_per_path = [set(h[1] for h in path) for path in path_lists]

    # 找所有路径的公共 IP
    common_ips = all_ips_per_path[0]
    for s in all_ips_per_path[1:]:
        common_ips = common_ips & s

    if not common_ips:
        # 没有完全公共的 IP，找至少出现在 2/3 路径中的 IP
        from collections import Counter
        ip_count = Counter()
        for path in path_lists:
            for hop in path:
                ip_count[hop[1]] += 1
        threshold = max(2, len(path_lists) * 2 // 3)
        common_ips = {ip for ip, cnt in ip_count.items() if cnt >= threshold}

    if not common_ips:
        return None, 0, {}

    # 找收敛点在路径中最早出现的位置
    earliest_hop = 999
    earliest_ip = None
    earliest_info = {}

    for path in path_lists:
        for hop in path:
            if hop[1] in common_ips:
                if hop[0] < earliest_hop:
                    earliest_hop = hop[0]
                    earliest_ip = hop[1]
                    earliest_info = {
                        "org": hop[2] or "",
                        "country": hop[3] or "",
                        "city": hop[4] or "",
                        "asn": hop[5] or "",
                    }
                break  # 每条路径只取第一个收敛点

    return earliest_ip, earliest_hop, earliest_info


def _save_result(result: dict):
    _ensure_convergence_table()
    now = datetime.now().isoformat()
    with get_conn() as db:
        db.execute("""
            INSERT OR REPLACE INTO convergence_results
            (target, convergence_ip, convergence_hop, convergence_org, convergence_country,
             node_count, nodes, confidence, tag, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result["target"],
            result.get("convergence_ip", ""),
            result.get("convergence_hop", 0),
            result.get("convergence_org", ""),
            result.get("convergence_country", ""),
            result.get("node_count", 0),
            json.dumps(result.get("nodes", []), ensure_ascii=False),
            result.get("confidence", 0),
            result.get("tag", ""),
            now,
        ))


def run_batch_analysis(min_nodes: int = 2) -> dict:
    """
    批量分析所有有多节点数据的目标 IP
    返回统计结果
    """
    _ensure_convergence_table()
    with get_conn() as db:
        c = db.cursor()
        targets = c.execute("""
            SELECT target, COUNT(DISTINCT agent_id) as node_cnt
            FROM traceroute_tasks
            GROUP BY target
            HAVING node_cnt >= ?
            ORDER BY node_cnt DESC
        """, (min_nodes,)).fetchall()

    logger.info(f"[Convergence] 开始分析 {len(targets)} 个目标")
    stats = {"total": len(targets), "proxy_chain": 0, "shared_upstream": 0,
             "anycast": 0, "diverse_paths": 0, "insufficient_data": 0}

    for target, node_cnt in targets:
        result = analyze_target(target)
        tag = result.get("tag", "insufficient_data")
        if tag in stats:
            stats[tag] += 1

    logger.info(f"[Convergence] 分析完成: {stats}")
    return stats


def get_convergence_summary() -> dict:
    """获取收敛分析汇总，供 insights 页面展示"""
    _ensure_convergence_table()
    with get_conn() as db:
        c = db.cursor()

        # 按 tag 统计
        tag_stats = dict(c.execute("""
            SELECT tag, COUNT(*) FROM convergence_results GROUP BY tag
        """).fetchall())

        # 最常见的收敛点（代理出口网关）
        top_gateways = c.execute("""
            SELECT convergence_ip, convergence_org, convergence_country,
                   COUNT(*) as target_count, AVG(confidence) as avg_conf
            FROM convergence_results
            WHERE convergence_ip != '' AND tag IN ('proxy_chain', 'shared_upstream')
            GROUP BY convergence_ip
            ORDER BY target_count DESC
            LIMIT 10
        """).fetchall()

        # 最近发现的代理链路
        recent_proxy = c.execute("""
            SELECT target, convergence_ip, convergence_org, convergence_hop,
                   node_count, confidence, analyzed_at
            FROM convergence_results
            WHERE tag IN ('proxy_chain', 'shared_upstream')
            ORDER BY analyzed_at DESC
            LIMIT 20
        """).fetchall()

    return {
        "tag_stats": tag_stats,
        "top_gateways": [
            {"ip": r[0], "org": r[1], "country": r[2],
             "target_count": r[3], "avg_confidence": round(r[4], 2)}
            for r in top_gateways
        ],
        "recent_proxy": [
            {"target": r[0], "gateway_ip": r[1], "gateway_org": r[2],
             "hop": r[3], "nodes": r[4], "confidence": r[5],
             "time": r[6][:16] if r[6] else ""}
            for r in recent_proxy
        ],
    }


# 初始化表
try:
    _ensure_convergence_table()
except Exception as e:
    logger.warning(f"[Convergence] 初始化失败: {e}")
