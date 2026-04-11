"""
扫描任务池
- IP 入队（来自 FOFA / 用户手动）
- 节点空闲时领取任务
- 任务状态管理：pending → running → done/failed
"""
import asyncio
import logging
from datetime import datetime, timedelta
from netcheck.trace_db import get_conn

logger = logging.getLogger(__name__)


def init_queue_table():
    with get_conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS scan_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ip          TEXT NOT NULL UNIQUE,
            priority    INTEGER DEFAULT 5,   -- 1=高(用户手动) 5=普通(FOFA) 9=低(变化检测)
            status      TEXT DEFAULT 'pending',  -- pending/running/done/failed
            source      TEXT DEFAULT 'fofa',
            assigned_agent TEXT DEFAULT '',
            assigned_at TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            done_at     TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_queue_status ON scan_queue(status, priority);
        CREATE INDEX IF NOT EXISTS idx_queue_ip ON scan_queue(ip);
        """)


def enqueue(ips: list, source: str = "fofa", priority: int = 5) -> int:
    """批量入队，已存在的跳过，返回新增数量"""
    now = datetime.now().isoformat()
    added = 0
    with get_conn() as db:
        for ip in ips:
            try:
                db.execute("""
                    INSERT OR IGNORE INTO scan_queue (ip, priority, source, created_at)
                    VALUES (?, ?, ?, ?)
                """, (ip, priority, source, now))
                if db.execute("SELECT changes()").fetchone()[0]:
                    added += 1
            except Exception:
                pass
    logger.info(f"[Queue] 入队 {added} 个新 IP（来源: {source}）")
    return added


def dequeue(agent_id: str, count: int = 1) -> list:
    """节点领取任务，返回 IP 列表"""
    now = datetime.now().isoformat()
    # 超时释放：running 超过10分钟的任务重置为 pending
    timeout = (datetime.now() - timedelta(minutes=10)).isoformat()
    with get_conn() as db:
        db.execute("""
            UPDATE scan_queue SET status='pending', assigned_agent='', assigned_at=''
            WHERE status='running' AND assigned_at < ?
        """, (timeout,))

        rows = db.execute("""
            SELECT id, ip FROM scan_queue
            WHERE status='pending'
            ORDER BY priority ASC, id ASC
            LIMIT ?
        """, (count,)).fetchall()

        if not rows:
            return []

        ids = [r[0] for r in rows]
        db.execute(f"""
            UPDATE scan_queue SET status='running', assigned_agent=?, assigned_at=?
            WHERE id IN ({','.join('?'*len(ids))})
        """, [agent_id, now] + ids)

    return [r[1] for r in rows]


def mark_done(ip: str, success: bool = True):
    now = datetime.now().isoformat()
    status = 'done' if success else 'failed'
    with get_conn() as db:
        db.execute("""
            UPDATE scan_queue SET status=?, done_at=? WHERE ip=?
        """, (status, now, ip))


def queue_stats() -> dict:
    with get_conn() as db:
        rows = db.execute("""
            SELECT status, COUNT(*) FROM scan_queue GROUP BY status
        """).fetchall()
    stats = {r[0]: r[1] for r in rows}
    return {
        "pending": stats.get("pending", 0),
        "running": stats.get("running", 0),
        "done": stats.get("done", 0),
        "failed": stats.get("failed", 0),
        "total": sum(stats.values()),
    }


def requeue_failed(max_retry: int = 3):
    """把失败次数未超限的任务重新入队"""
    with get_conn() as db:
        db.execute("""
            UPDATE scan_queue SET status='pending', retry_count=retry_count+1
            WHERE status='failed' AND retry_count < ?
        """, (max_retry,))


# 启动时初始化
try:
    init_queue_table()
except Exception as e:
    logger.warning(f"[Queue] 初始化失败: {e}")
