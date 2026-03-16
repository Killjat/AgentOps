#!/bin/bash
# AgentOps 启动脚本

echo "=================================="
echo "AgentOps - AI-Powered Operations"
echo "=================================="

# 检查 Python 版本
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python 版本: $python_version"

# 检查依赖
echo ""
echo "检查依赖..."
pip list | grep -q fastapi && echo "✓ fastapi" || echo "✗ fastapi (运行: pip install fastapi)"
pip list | grep -q uvicorn && echo "✓ uvicorn" || echo "✗ uvicorn (运行: pip install uvicorn)"
pip list | grep -q aiohttp && echo "✓ aiohttp" || echo "✗ aiohttp (运行: pip install aiohttp)"
pip list | grep -q websockets && echo "✓ websockets" || echo "✗ websockets (运行: pip install websockets)"
pip list | grep -q pydantic && echo "✓ pydantic" || echo "✗ pydantic (运行: pip install pydantic)"

# 检查环境变量
echo ""
echo "检查 API Keys..."
if [ -n "$DEEPSEEK_API_KEY" ]; then
    echo "✓ DEEPSEEK_API_KEY 已配置"
else
    echo "✗ DEEPSEEK_API_KEY 未配置"
fi

if [ -n "$GROK_API_KEY" ]; then
    echo "✓ GROK_API_KEY 已配置"
else
    echo "  GROK_API_KEY 未配置（可选）"
fi

if [ -n "$OPENAI_API_KEY" ]; then
    echo "✓ OPENAI_API_KEY 已配置"
else
    echo "  OPENAI_API_KEY 未配置（可选）"
fi

if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "✓ ANTHROPIC_API_KEY 已配置"
else
    echo "  ANTHROPIC_API_KEY 未配置（可选）"
fi

# 检查是否至少配置了一个 API Key
if [ -z "$DEEPSEEK_API_KEY" ] && [ -z "$GROK_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "⚠️  警告: 未配置任何 API Key"
    echo "请设置至少一个 API Key:"
    echo "  export DEEPSEEK_API_KEY='your-api-key'"
    echo ""
    echo "或创建 .env 文件（参考 .env.example）"
    echo ""
    read -p "是否继续启动? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "=================================="
echo "启动服务器..."
echo "=================================="
echo ""

# 启动服务器
python3 linux_agent_multi.py
