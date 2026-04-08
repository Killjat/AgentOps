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
- 只能给 status 为 online 的 Agent 分配任务，offline 的 Agent 不得出现在 subtasks 中
- Android 设备运行在 Termux 环境，只能执行 shell 命令，可用工具包括：curl、wget、ping、nslookup、traceroute、getprop、cat /proc/*、df、ps、ss、ip addr 等；不能执行任何 UI 操作，不能打开浏览器，不能访问需要 root 权限的路径；查询 WiFi 信息用 /system/bin/cmd wifi status，不要用 dumpsys wifi
- Windows 设备只能使用 PowerShell 或 cmd 原生命令，不能使用 awk、sed、grep、curl（用 Invoke-WebRequest 替代）等 Linux 工具；ping 用 ping -n，查网络用 netstat、ipconfig
- 生成的 shell 命令必须完整，不能截断，命令长度没有限制
- traceroute 任务只用 `traceroute -n www.example.com` 不加任何 grep/awk 过滤，让原始输出返回
- 如果是 Linux 系统，traceroute 命令用 `traceroute -n TARGET 2>/dev/null || tracepath -n TARGET 2>/dev/null || mtr -n --report --report-cycles 3 TARGET 2>/dev/null`，三个命令依次 fallback（TARGET 替换为实际域名）
- Windows 系统 traceroute 用 `tracert -d -h 20 TARGET`，不要加任何引号或额外参数（TARGET 替换为实际域名）
- Android 系统没有 traceroute，用 `ping -c 5 TARGET` 代替（TARGET 替换为实际域名）
"""


async def plan_swarm_task(task: SwarmTask, agents_info: List[Dict], context: str = "") -> SwarmTask:
    """调用 LLM 生成执行计划，填充 subtasks"""
    agents_desc = "\n".join(
        f"- {a['agent_id']}: {a.get('os_type', 'unknown')} | {a.get('hostname', '')} | {a.get('status', 'online')}"
        for a in agents_info
    )

    context_section = ""
    if context:
        context_section = f"\n\n上一次任务的执行结果（供参考）：\n{context}\n"

    # 注入历史成功案例
    from knowledge import get_relevant_examples
    examples = get_relevant_examples(task.goal)
    examples_section = f"\n\n{examples}\n" if examples else ""

    messages = [
        {"role": "user", "content": PLAN_PROMPT.format(
            goal=task.goal,
            agents_info=agents_desc,
        ) + context_section + examples_section}
    ]

    raw = await chat(messages, max_tokens=2000)

    # 提取 JSON
    try:
        # 去掉可能的 markdown 代码块
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # LLM 有时在 JSON 字符串里生成未转义的反斜杠，尝试修复
            import re
            fixed = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', raw)
            data = json.loads(fixed)
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
