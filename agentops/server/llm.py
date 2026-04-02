"""LLM 调用封装 - 命令生成 + 结果分析"""
import os
import re
import aiohttp
import ssl
from typing import Optional
from models import LLMProvider

LLM_CONFIGS = {
    LLMProvider.DEEPSEEK: {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    LLMProvider.OPENAI: {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o",
        "key_env": "OPENAI_API_KEY",
    },
    LLMProvider.GROK: {
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-beta",
        "key_env": "GROK_API_KEY",
    },
    LLMProvider.ANTHROPIC: {
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-3-5-sonnet-20241022",
        "key_env": "ANTHROPIC_API_KEY",
    },
}

GENERATE_PROMPT = """你是一个 {os_type} 系统运维专家。
用户用自然语言描述需求，你只返回可直接执行的命令，不要任何解释。
多个命令用 && 连接。如果任务不明确，返回 NEED_CLARIFICATION: <问题>。

示例：
用户：查看磁盘使用情况
助手：df -h

用户：查看开放端口
助手：ss -tuln"""

ANALYZE_PROMPT = """你是一个运维专家，请简要分析以下命令执行结果（不超过150字）：
任务：{task}
命令：{command}
状态：{status}
输出：
{output}

分析要点：结果是否正常？有无异常？关键信息是什么？"""


def _get_api_key(provider: LLMProvider) -> str:
    cfg = LLM_CONFIGS[provider]
    key = os.getenv(cfg["key_env"])
    if not key:
        raise ValueError(f"未配置 {cfg['key_env']} 环境变量")
    return key


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _call_api(provider: LLMProvider, messages: list, max_tokens: int = 300) -> str:
    """统一 LLM API 调用"""
    cfg = LLM_CONFIGS[provider]
    api_key = _get_api_key(provider)

    connector = aiohttp.TCPConnector(ssl=_make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        if provider == LLMProvider.ANTHROPIC:
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            # Anthropic 的 system 消息单独传
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            user_msgs = [m for m in messages if m["role"] != "system"]
            data = {"model": cfg["model"], "max_tokens": max_tokens,
                    "system": system, "messages": user_msgs}
        else:
            headers = {"Authorization": f"Bearer {api_key}",
                       "Content-Type": "application/json"}
            data = {"model": cfg["model"], "messages": messages,
                    "temperature": 0.1, "max_tokens": max_tokens}

        async with session.post(cfg["url"], headers=headers, json=data, timeout=60) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"LLM API 错误 ({resp.status}): {text[:200]}")
            result = await resp.json()

    if provider == LLMProvider.ANTHROPIC:
        return result["content"][0]["text"].strip()
    else:
        content = result["choices"][0]["message"]["content"].strip()
        # 去掉 DeepSeek R1 的思考标记
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return content


async def generate_command(task: str, os_type: str = "Linux",
                           provider: Optional[LLMProvider] = None) -> str:
    """根据自然语言生成命令"""
    provider = provider or _detect_provider()
    messages = [
        {"role": "system", "content": GENERATE_PROMPT.format(os_type=os_type)},
        {"role": "user", "content": task},
    ]
    return await _call_api(provider, messages, max_tokens=200)


async def analyze_result(task: str, command: str, output: str, success: bool,
                         provider: Optional[LLMProvider] = None) -> str:
    """分析命令执行结果"""
    provider = provider or _detect_provider()
    prompt = ANALYZE_PROMPT.format(
        task=task, command=command,
        status="成功" if success else "失败",
        output=output[:800],
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        return await _call_api(provider, messages, max_tokens=300)
    except Exception as e:
        return f"分析失败: {e}"


def _detect_provider() -> LLMProvider:
    """按优先级自动选择已配置的 Provider"""
    for p in [LLMProvider.DEEPSEEK, LLMProvider.OPENAI,
              LLMProvider.GROK, LLMProvider.ANTHROPIC]:
        if os.getenv(LLM_CONFIGS[p]["key_env"]):
            return p
    raise ValueError("未配置任何 LLM API Key，请设置环境变量")
