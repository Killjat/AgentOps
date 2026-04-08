"""SQLite 数据库 - 存储 swarm 任务历史"""
import json
import sqlite3
import logging
from pathlib import Path
from typing import List, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_FILE = Path(__file__).parent.parent.parent / "cyberagentops.db"


@contextmanager
def get_conn():
    """上下文管理器，确保连接自动关闭"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """初始化数据库表"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS swarm_tasks (
            swarm_task_id TEXT PRIMARY KEY,
            owner         TEXT,
            goal          TEXT,
            plan          TEXT,
            status        TEXT,
            summary       TEXT,
            agent_ids     TEXT,
            subtasks      TEXT,
            created_at    TEXT,
            completed_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_swarm_created ON swarm_tasks(created_at DESC);
        """)
    logger.info(f"[DB] 初始化完成: {DB_FILE}")


# ── Swarm Tasks ──────────────────────────────────────────────────

def save_swarm_task(task) -> None:
    """保存或更新 swarm 任务"""
    try:
        data = task.dict() if hasattr(task, 'dict') else task.model_dump()
        with get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO swarm_tasks
                (swarm_task_id, owner, goal, plan, status, summary, agent_ids, subtasks, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["swarm_task_id"],
                data.get("owner", ""),
                data.get("goal", ""),
                data.get("plan", ""),
                str(data.get("status", "")),
                data.get("summary", ""),
                json.dumps(data.get("agent_ids", []), ensure_ascii=False),
                json.dumps(data.get("subtasks", []), ensure_ascii=False, default=str),
                data.get("created_at", ""),
                data.get("completed_at", ""),
            ))
    except Exception as e:
        logger.error(f"[DB] 保存 swarm 任务失败: {e}")


def load_swarm_tasks() -> List[dict]:
    """加载所有 swarm 任务"""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM swarm_tasks ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["agent_ids"] = json.loads(d["agent_ids"] or "[]")
            d["subtasks"] = json.loads(d["subtasks"] or "[]")
            result.append(d)
        return result
    except Exception as e:
        logger.error(f"[DB] 加载 swarm 任务失败: {e}")
        return []


def get_swarm_task(swarm_task_id: str) -> Optional[dict]:
    """获取单个 swarm 任务"""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM swarm_tasks WHERE swarm_task_id = ?",
                (swarm_task_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["agent_ids"] = json.loads(d["agent_ids"] or "[]")
        d["subtasks"] = json.loads(d["subtasks"] or "[]")
        return d
    except Exception as e:
        logger.error(f"[DB] 获取 swarm 任务失败: {e}")
        return None
