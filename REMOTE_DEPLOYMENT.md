# Linux 运维 Agent - 远程部署指南

## 🎯 架构概览

```
客户端 (你的电脑/手机)
    ↓ HTTP/WebSocket
服务器 (Linux 服务器)
    ↓ 本地调用
DeepSeek 模型 (Ollama)
    ↓ 生成命令
Linux 系统执行
```

## 🚀 快速部署

### 服务器端部署

#### 1. 安装依赖

```bash
# 安装 Python 依赖
pip install fastapi uvicorn aiohttp websockets pydantic

# 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 下载 DeepSeek 模型
ollama pull deepseek-r1:7b
```

#### 2. 启动 Ollama

```bash
# 启动 Ollama 服务
ollama serve

# 或者后台运行
nohup ollama serve > ollama.log 2>&1 &
```

#### 3. 启动 Agent 服务器

```bash
# 前台运行（测试用）
python linux_agent_server.py

# 后台运行（生产环境）
nohup python linux_agent_server.py > agent.log 2>&1 &

# 或使用 uvicorn 直接启动
uvicorn linux_agent_server:app --host 0.0.0.0 --port 8000
```
