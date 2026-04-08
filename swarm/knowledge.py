"""Swarm 知识库 - 从历史成功任务中学习，为 planner 提供参考案例"""
import json
import os
from pathlib import Path
from typing import List, Dict

KNOWLEDGE_FILE = Path(__file__).parent.parent / "swarm_knowledge.json"
MAX_ENTRIES = 200  # 最多保留200条


def _load() -> List[Dict]:
    if not KNOWLEDGE_FILE.exists():
        return []
    try:
        return json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(entries: List[Dict]):
    KNOWLEDGE_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def record_success(task) -> None:
    """任务完成后，将成功的子任务提取为知识条目"""
    from swarm_models import SubTaskStatus
    entries = _load()

    for st in task.subtasks:
        if st.status != SubTaskStatus.SUCCESS:
            continue
        if not st.command or not st.output:
            continue

        entry = {
            "goal": task.goal,
            "os_type": _guess_os(st.agent_id, task),
            "instruction": st.instruction,
            "command": st.command,
            "output_preview": (st.output or "")[:200],
        }
        # 去重：相同 goal + command 不重复记录
        exists = any(
            e.get("goal") == entry["goal"] and e.get("command") == entry["command"]
            for e in entries
        )
        if not exists:
            entries.insert(0, entry)

    # 限制条数
    entries = entries[:MAX_ENTRIES]
    _save(entries)


def _guess_os(agent_id: str, task) -> str:
    """从 agent_id 猜测 OS 类型"""
    if "android" in agent_id:
        return "android"
    if "win" in agent_id:
        return "windows"
    if "mac" in agent_id:
        return "macos"
    return "linux"


def get_relevant_examples(goal: str, limit: int = 5) -> str:
    """根据目标关键词找相关历史案例，返回 prompt 片段"""
    entries = _load()
    if not entries:
        return ""

    # 简单关键词匹配
    goal_words = set(goal.lower().split())
    scored = []
    for e in entries:
        entry_words = set((e.get("goal", "") + " " + e.get("instruction", "")).lower().split())
        score = len(goal_words & entry_words)
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [e for _, e in scored[:limit]]

    if not top:
        return ""

    lines = ["以下是历史成功案例，可作为参考（命令已验证可用）："]
    for e in top:
        lines.append(f"- 目标「{e['goal']}」→ [{e.get('os_type','linux')}] `{e['command']}`")

    return "\n".join(lines)
