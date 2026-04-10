"""
Traceroute 数据存储层
当前用 SQLite，后续可无缝切换到 MySQL
只需修改 get_conn() 函数
"""
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "cyberagentops.db"


def get_conn():
    """获取数据库连接 - 切换 MySQL 只需改这里"""
    # MySQL 版本（备用）:
    # import pymysql
    # return pymysql.connect(host='127.0.0.1', port=3306,
    #     user='root', password='Cyber2024!', db='cybernetcheck',
    #     charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_trace_tables():
    """初始化 traceroute 相关表"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS traceroute_tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT NOT NULL,
            target      TEXT NOT NULL,          -- 目标 IP 或域名
            target_type TEXT DEFAULT 'ip',      -- ip / domain
            source      TEXT DEFAULT 'probe',   -- probe / recon / batch
            agent_id    TEXT,
            agent_name  TEXT,
            os_type     TEXT,
            total_hops  INTEGER DEFAULT 0,
            valid_hops  INTEGER DEFAULT 0,
            timeout_hops INTEGER DEFAULT 0,
            last_latency_ms REAL DEFAULT 0,
            created_at  TEXT NOT NULL,
            duration_ms INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS traceroute_hops (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT NOT NULL,
            hop_index   INTEGER NOT NULL,
            ip          TEXT NOT NULL,
            country     TEXT DEFAULT '',
            city        TEXT DEFAULT '',
            org         TEXT DEFAULT '',
            asn         TEXT DEFAULT '',
            tag         TEXT DEFAULT '',        -- 机房/住宅/骨干/内网/超时
            latency_ms  REAL DEFAULT 0,
            is_last_hop INTEGER DEFAULT 0,      -- 1=最后一跳
            is_private  INTEGER DEFAULT 0,      -- 1=内网IP
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ip_profiles (
            ip          TEXT PRIMARY KEY,
            asn         TEXT DEFAULT '',
            org         TEXT DEFAULT '',
            country     TEXT DEFAULT '',
            city        TEXT DEFAULT '',
            tag         TEXT DEFAULT '',
            is_datacenter INTEGER DEFAULT 0,
            is_residential INTEGER DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT,
            seen_count  INTEGER DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_target ON traceroute_tasks(target);
        CREATE INDEX IF NOT EXISTS idx_tasks_created ON traceroute_tasks(created_at);
        CREATE INDEX IF NOT EXISTS idx_hops_task ON traceroute_hops(task_id);
        CREATE INDEX IF NOT EXISTS idx_hops_ip ON traceroute_hops(ip);
        CREATE INDEX IF NOT EXISTS idx_hops_last ON traceroute_hops(is_last_hop);
        """)


def save_traceroute(task_id: str, target: str, target_type: str,
                    source: str, agent_id: str, agent_name: str,
                    os_type: str, hops: list, last_latency_ms: float = 0):
    """
    保存一次 traceroute 结果
    hops: enrich_hops 返回的富化跳点列表
    """
    now = datetime.now().isoformat()
    valid = [h for h in hops if h.get("ip") != "*" and h.get("ip")]
    timeout = sum(1 for h in hops if h.get("ip") == "*")

    with get_conn() as conn:
        # 保存任务
        conn.execute("""
            INSERT OR REPLACE INTO traceroute_tasks
            (task_id, target, target_type, source, agent_id, agent_name,
             os_type, total_hops, valid_hops, timeout_hops, last_latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task_id, target, target_type, source, agent_id, agent_name,
              os_type, len(hops), len(valid), timeout, last_latency_ms, now))

        # 保存每个跳点
        for i, hop in enumerate(hops):
            ip = hop.get("ip", "")
            is_last = 1 if i == len(hops) - 1 else 0
            is_private = 1 if hop.get("country") == "内网" or _is_private(ip) else 0
            asn = ""
            org = hop.get("org", "")
            if org and org.startswith("AS"):
                parts = org.split(" ", 1)
                asn = parts[0]
                org = parts[1] if len(parts) > 1 else org

            conn.execute("""
                INSERT INTO traceroute_hops
                (task_id, hop_index, ip, country, city, org, asn, tag,
                 latency_ms, is_last_hop, is_private, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (task_id, i + 1, ip,
                  hop.get("country", ""), hop.get("city", ""),
                  org, asn, hop.get("tag", ""),
                  hop.get("avg", 0) or 0,
                  is_last, is_private, now))

            # 更新 ip_profiles（只记录公网 IP）
            if ip and ip != "*" and not is_private:
                tag = hop.get("tag", "")
                is_dc = 1 if "机房" in tag else 0
                is_res = 1 if "住宅" in tag else 0
                conn.execute("""
                    INSERT INTO ip_profiles (ip, asn, org, country, city, tag,
                        is_datacenter, is_residential, first_seen, last_seen, seen_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(ip) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        seen_count = seen_count + 1,
                        tag = CASE WHEN excluded.tag != '' THEN excluded.tag ELSE tag END
                """, (ip, asn, org, hop.get("country", ""), hop.get("city", ""),
                      tag, is_dc, is_res, now, now))


def get_last_hops_for_target(target: str, limit: int = 10) -> list:
    """查询某目标最近几次的最后几跳，用于快速展示历史数据"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT h.ip, h.country, h.city, h.org, h.tag, h.latency_ms,
                   t.agent_name, t.created_at
            FROM traceroute_hops h
            JOIN traceroute_tasks t ON h.task_id = t.task_id
            WHERE t.target = ? AND h.is_last_hop = 1
            ORDER BY t.created_at DESC
            LIMIT ?
        """, (target, limit)).fetchall()
        return [dict(r) for r in rows]


def get_ip_profile(ip: str) -> dict:
    """查询 IP 画像"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ip_profiles WHERE ip = ?", (ip,)
        ).fetchone()
        return dict(row) if row else {}


def _is_private(ip: str) -> bool:
    import re
    patterns = [r'^10\.', r'^192\.168\.', r'^172\.(1[6-9]|2\d|3[01])\.', r'^127\.']
    return any(re.match(p, ip or "") for p in patterns)


# 启动时初始化表
try:
    init_trace_tables()
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f"trace_db init failed: {e}")
