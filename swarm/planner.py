"""Swarm Planner - 用 LLM 将目标拆解为多 Agent 子任务"""
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from llm import chat
from swarm_models import SwarmTask, SubTask
from typing import List, Dict




PLAN_PROMPT = """你是一个多 Agent 任务调度专家。
用户有一个目标需要多台机器协作完成，请将目标拆解为子任务并分配给对应的 Agent。

目标：{goal}

可用 Agent 列表：
{agents_info}

请返回 JSON 格式的任务计划，格式如下：
{{
  "plan": "整体执行思路（一句话）",
  "subtasks": [
    {{
      "agent_id": "agent-xxx",
      "instruction": "自然语言指令",
      "depends_on": []
    }}
  ]
}}

规则：
- 每个子任务分配给最合适的 Agent
- depends_on 填写前置子任务的索引（从0开始），空数组表示可并行
- 只返回 JSON，不要其他内容
- 重要：每个子任务必须是独立完整的，不能依赖其他子任务的输出结果
- 每个子任务的 instruction 必须是可以一步完成的操作，例如"用curl抓取URL并用grep提取标题"而不是分成"抓取"和"解析"两步
- 如果任务需要抓取并解析数据，必须在一条指令中完成，例如：curl抓取后用grep/sed/awk直接提取所需内容
"""


async def plan_swarm_task(task: SwarmTask, agents_info: List[Dict]) -> SwarmTask:
    """调用 LLM 生成执行计划，填充 subtasks"""
    agents_desc = "\n".join(
        f"- {a['agent_id']}: {a.get('os_type', 'unknown')} | {a.get('hostname', '')} | {a.get('status', 'online')}"
        for a in agents_info
    )

    messages = [
        {"role": "user", "content": PLAN_PROMPT.format(
            goal=task.goal,
            agents_info=agents_desc,
        )}
    ]

    raw = await chat(messages, max_tokens=1000)

    # 提取 JSON
    try:
        # 去掉可能的 markdown 代码块
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
    except Exception as e:
        task.plan = f"计划解析失败: {e}\n原始输出: {raw[:200]}"
        return task

    task.plan = data.get("plan", "")

    for i, st in enumerate(data.get("subtasks", [])):
        # depends_on 支持索引或 subtask_id
        depends = [
            f"{task.swarm_task_id}-sub-{d}" for d in st.get("depends_on", [])
        ]
        subtask = SubTask(
            subtask_id=f"{task.swarm_task_id}-sub-{i}",
            swarm_task_id=task.swarm_task_id,
            agent_id=st["agent_id"],
            instruction=st["instruction"],
            depends_on=depends,
        )
        task.subtasks.append(subtask)

    return task
