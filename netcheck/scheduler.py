"""
自动化分析调度引擎
- 定时从 FOFA 拉取新 IP
- 批量 traceroute
- 自动分析：ASN 聚类、基础设施关联、变化检测
- 结果写入数据库，供 insights 页面展示
"""
import asyncio
import base64
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List

import aiohttp

logger = logging.getLogger(__name__)

# ── FOFA 预设查询任务 ─────────────────────────────────────────
# 每个任务定义：查询语句、执行间隔（小时）、最大拉取数量
FOFA_JOBS = [
    {"name": "Shadowsocks节点",   "query": 'protocol="shadowsocks"',          "interval_h": 24, "size": 100},
    {"name": "VMess节点",          "query": 'protocol="vmess"',                "interval_h": 24, "size": 100},
    {"name": "Clash面板",          "query": 'app="Clash" && title="Clash"',    "interval_h": 48, "size": 50},
    {"name": "国内VPN产品",        "query": 'category="VPN产品" && country="CN"', "interval_h": 48, "size": 50},
    {"name": "HK代理节点",         "query": 'protocol="shadowsocks" && country="HK"', "interval_h": 12, "size": 80},
]


class AnalysisScheduler:
    def __init__(self):
        self.running = False
        self._fofa_email = os.getenv("FOFA_EMAIL", "")
        self._fofa_key = os.getenv("FOFA_KEY", "")

    async def start(self):
        """启动调度器，后台持续运行"""
        self.running = True
        logger.info("[Scheduler] 启动自动化分析调度器")
        await asyncio.gather(
            self._fofa_crawl_loop(),
            self._queue_consumer_loop(),
            self._analysis_loop(),
            self._change_detect_loop(),
        )

    def stop(self):
        self.running = False

    # ── FOFA 定时爬取 ─────────────────────────────────────────

    async def _fofa_crawl_loop(self):
        """定时从 FOFA 拉取新 IP 放入任务队列"""
        while self.running:
            for job in FOFA_JOBS:
                if not self._should_run(job["name"], job["interval_h"]):
                    continue
                try:
                    logger.info(f"[Scheduler] FOFA 爬取: {job['name']}")
                    ips = await self._fofa_fetch(job["query"], job["size"])
                    if ips:
                        from netcheck.queue import enqueue
                        added = enqueue(ips, source=job["name"], priority=5)
                        logger.info(f"[Scheduler] {job['name']} 入队 {added} 个新 IP")
                    self._mark_job_run(job["name"])
                except Exception as e:
                    logger.warning(f"[Scheduler] FOFA 爬取失败 {job['name']}: {e}")
            await asyncio.sleep(300)

    async def _queue_consumer_loop(self):
        """消费任务队列：每次取1个 IP，用多个节点同时扫"""
        await asyncio.sleep(10)
        while self.running:
            try:
                from core.state import agents as _agents
                from netcheck.queue import dequeue, mark_done, queue_stats

                stats = queue_stats()
                if stats["pending"] == 0:
                    await asyncio.sleep(30)
                    continue

                online = [a for a in _agents.values() if a.status == "online"]
                if not online:
                    await asyncio.sleep(30)
                    continue

                # 取1个 IP，用所有在线节点同时扫（不设上限，保证多视角）
                ips = dequeue("scheduler", count=1)
                if not ips:
                    await asyncio.sleep(10)
                    continue

                ip = ips[0]
                # 所有在线节点都参与，慢一点没关系，数据完整性更重要
                agent_ids = [a.agent_id for a in online]

                logger.info(f"[Queue] 扫描 {ip}，全部 {len(agent_ids)} 个节点参与，队列剩余 {stats['pending']-1}")

                try:
                    from netcheck.router import _run_scan, _scan_tasks
                    import uuid
                    task_id = f"q-{uuid.uuid4().hex[:8]}"
                    _scan_tasks[task_id] = {
                        "task_id": task_id, "target_ip": ip,
                        "status": "running",
                        "created_at": datetime.now().isoformat(),
                        "results": [], "ip_profile": {}, "completed_at": "",
                    }
                    # 所有节点并发 traceroute 同一个 IP
                    await _run_scan(task_id, ip, agent_ids)
                    mark_done(ip, success=True)
                except Exception as e:
                    mark_done(ip, success=False)
                    logger.warning(f"[Queue] 任务失败 {ip}: {e}")

                await asyncio.sleep(10)  # 等所有节点完成后再取下一个

            except Exception as e:
                logger.warning(f"[Queue] 消费循环异常: {e}")
                await asyncio.sleep(30)

    async def _fofa_fetch(self, query: str, size: int) -> List[str]:
        """从 FOFA 拉取 IP 列表"""
        if not self._fofa_email or not self._fofa_key:
            return []
        qb64 = base64.b64encode(query.encode()).decode()
        url = (f"https://fofa.info/api/v1/search/all"
               f"?email={self._fofa_email}&key={self._fofa_key}"
               f"&qbase64={qb64}&size={size}&fields=ip")
        import ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
            async with s.get(url, headers={"User-Agent": "Mozilla/5.0"},
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                d = await r.json()
                return list(dict.fromkeys(row[0] for row in d.get("results", []) if row and row[0]))

    async def _batch_traceroute(self, ips: List[str], source: str):
        """对一批 IP 发起 traceroute，复用现有的 probe/scan 逻辑"""
        from netcheck.router import _run_scan, _scan_tasks
        from core.state import agents as _agents
        import uuid

        online = [a for a in _agents.values() if a.status == "online"]
        if not online:
            logger.warning("[Scheduler] 无在线节点，跳过 traceroute")
            return

        # 最多选3个节点，优先选不同类型
        agent_ids = _select_diverse_agents(online, max_count=3)

        # 并发控制：最多同时跑5个
        sem = asyncio.Semaphore(5)

        async def scan_one(ip):
            async with sem:
                task_id = f"auto-{uuid.uuid4().hex[:8]}"
                _scan_tasks[task_id] = {
                    "task_id": task_id, "target_ip": ip,
                    "status": "running",
                    "created_at": datetime.now().isoformat(),
                    "results": [], "ip_profile": {}, "completed_at": "",
                    "source": source,
                }
                await _run_scan(task_id, ip, agent_ids)

        await asyncio.gather(*[scan_one(ip) for ip in ips])
        logger.info(f"[Scheduler] 完成 {len(ips)} 个 IP 的 traceroute")

    # ── 自动分析 ──────────────────────────────────────────────

    async def _analysis_loop(self):
        """每小时运行一次深度分析，更新 analysis_results 表"""
        while self.running:
            await asyncio.sleep(3600)
            try:
                await self._run_analysis()
            except Exception as e:
                logger.warning(f"[Scheduler] 分析失败: {e}")

    async def _run_analysis(self):
        """核心分析逻辑：ASN 聚类 + 路径收敛检测"""
        from netcheck.trace_db import get_conn
        logger.info("[Scheduler] 开始自动分析...")

        # 1. 路径收敛检测（核心分析）
        try:
            from netcheck.convergence import run_batch_analysis
            stats = run_batch_analysis(min_nodes=2)
            logger.info(f"[Scheduler] 收敛分析完成: {stats}")
        except Exception as e:
            logger.warning(f"[Scheduler] 收敛分析失败: {e}")

        with get_conn() as db:
            c = db.cursor()

            # 1. ASN 聚类：找出共享同一上游 ASN 的目标 IP 组
            c.execute("""
                SELECT h.asn, h.org, GROUP_CONCAT(DISTINCT t.target) as targets, COUNT(*) as cnt
                FROM traceroute_hops h
                JOIN traceroute_tasks t ON h.task_id = t.task_id
                WHERE h.asn != '' AND h.is_private = 0 AND h.hop_index >= 6
                GROUP BY h.asn
                HAVING cnt >= 3
                ORDER BY cnt DESC
            """)
            asn_clusters = c.fetchall()

            # 2. 基础设施关联：同一 ASN 下的目标 IP 可能属于同一服务商
            infra_groups = []
            for row in asn_clusters[:10]:
                asn, org, targets_str, cnt = row
                targets = targets_str.split(",") if targets_str else []
                if len(targets) >= 2:
                    infra_groups.append({
                        "asn": asn,
                        "org": org or "",
                        "target_count": len(targets),
                        "targets": targets[:20],
                        "hop_count": cnt,
                    })

            # 3. 写入分析结果表
            _ensure_analysis_table(db)
            now = datetime.now().isoformat()
            db.execute("DELETE FROM analysis_results WHERE type='asn_cluster'")
            for g in infra_groups:
                import json
                db.execute("""
                    INSERT INTO analysis_results (type, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                """, ("asn_cluster", g["asn"], json.dumps(g, ensure_ascii=False), now))

            logger.info(f"[Scheduler] 分析完成，发现 {len(infra_groups)} 个 ASN 集群")

    # ── 变化检测 ──────────────────────────────────────────────

    async def _change_detect_loop(self):
        """每6小时检测一次：已知 IP 的路由路径是否发生变化"""
        while self.running:
            await asyncio.sleep(21600)
            try:
                await self._detect_changes()
            except Exception as e:
                logger.warning(f"[Scheduler] 变化检测失败: {e}")

    async def _detect_changes(self):
        """对已探测过的 IP 重新扫描，对比路由变化"""
        from netcheck.trace_db import get_conn
        with get_conn() as db:
            c = db.cursor()
            # 找出超过7天没有重新扫描的目标
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            rows = c.execute("""
                SELECT target, MAX(created_at) as last_scan, COUNT(*) as scan_count
                FROM traceroute_tasks
                GROUP BY target
                HAVING last_scan < ? AND scan_count >= 2
                ORDER BY last_scan ASC
                LIMIT 20
            """, (cutoff,)).fetchall()

        if rows:
            ips = [r[0] for r in rows]
            logger.info(f"[Scheduler] 变化检测：重新扫描 {len(ips)} 个 IP")
            await self._batch_traceroute(ips, "change_detect")

    # ── 工具函数 ──────────────────────────────────────────────

    def _should_run(self, job_name: str, interval_h: int) -> bool:
        """检查任务是否到了执行时间"""
        from netcheck.trace_db import get_conn
        try:
            with get_conn() as db:
                _ensure_scheduler_table(db)
                row = db.execute(
                    "SELECT last_run FROM scheduler_jobs WHERE name=?", (job_name,)
                ).fetchone()
                if not row:
                    return True
                last_run = datetime.fromisoformat(row[0])
                return datetime.now() - last_run > timedelta(hours=interval_h)
        except Exception:
            return True

    def _mark_job_run(self, job_name: str):
        from netcheck.trace_db import get_conn
        with get_conn() as db:
            _ensure_scheduler_table(db)
            db.execute("""
                INSERT OR REPLACE INTO scheduler_jobs (name, last_run)
                VALUES (?, ?)
            """, (job_name, datetime.now().isoformat()))

    def _filter_new_ips(self, ips: List[str]) -> List[str]:
        """过滤掉最近24小时内已扫描过的 IP"""
        from netcheck.trace_db import get_conn
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        with get_conn() as db:
            existing = set(
                r[0] for r in db.execute(
                    "SELECT DISTINCT target FROM traceroute_tasks WHERE created_at > ?", (cutoff,)
                ).fetchall()
            )
        return [ip for ip in ips if ip not in existing]


def _select_diverse_agents(agents, max_count=5):
    """选取多样化节点：境外Linux + 国内Linux + Android移动 + Windows，覆盖不同视角"""
    selected = []

    # 1. 境外 Linux（美国/香港）
    for a in agents:
        os_t = str(getattr(a.os_type, 'value', a.os_type)).lower()
        name = (a.name or "").lower()
        if "linux" in os_t and any(k in name for k in ["美国", "香港", "us", "hk"]):
            selected.append(a.agent_id)
            break

    # 2. 国内 Linux
    for a in agents:
        if a.agent_id in selected: continue
        os_t = str(getattr(a.os_type, 'value', a.os_type)).lower()
        name = (a.name or "").lower()
        if "linux" in os_t and any(k in name for k in ["阿里", "cn", "china"]):
            selected.append(a.agent_id)
            break

    # 3. Android 移动端（国内运营商视角，最有价值）
    for a in agents:
        if a.agent_id in selected: continue
        os_t = str(getattr(a.os_type, 'value', a.os_type)).lower()
        if "android" in os_t:
            selected.append(a.agent_id)
            if len([x for x in selected if "android" in str(getattr(
                next((ag for ag in agents if ag.agent_id == x), None).os_type, '').lower()
            )]) >= 2:
                break  # 最多2个 Android

    # 4. 补足其他节点
    for a in agents:
        if len(selected) >= max_count: break
        if a.agent_id not in selected:
            selected.append(a.agent_id)

    return selected[:max_count]


def _ensure_scheduler_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_jobs (
            name TEXT PRIMARY KEY,
            last_run TEXT NOT NULL
        )
    """)


def _ensure_analysis_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis_results(type)")


# 全局单例
_scheduler = AnalysisScheduler()


async def start_scheduler():
    """在 FastAPI 启动时调用"""
    asyncio.create_task(_scheduler.start())
