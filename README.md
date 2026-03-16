# AgentOps

**AI-Powered Operations, Simplified**

基于 DeepSeek 大模型的智能运维系统，支持 Multi-Agent 协作和 AI 结果分析。

```
   ___                    _    ___            
  / _ \                  | |  / _ \           
 / /_\ \ __ _  ___ _ __ | |_| | | |_ __  ___ 
 |  _  |/ _` |/ _ \ '_ \| __| | | | '_ \/ __|
 | | | | (_| |  __/ | | | |_| |_| | |_) \__ \
 \_| |_/\__, |\___|_| |_|\__|\___/| .__/|___/
         __/ |                    | |        
        |___/                     |_|        
```

## 🎯 核心功能

- ✅ **自然语言交互** - 用人话下发运维任务
- ✅ **智能命令生成** - DeepSeek 自动生成 Linux 命令
- ✅ **AI 结果分析** - Agent 执行完任务后自动询问大模型分析结果 ⭐ NEW
- ✅ **安全执行** - 危险命令拦截和确认机制
- ✅ **远程任务下发** - REST API + WebSocket 实时通信
- ✅ **Multi-Agent 管理** - 多个专业化 Agent，每个有独立编号和角色
- ✅ **角色专业化** - 监控、安全、网络、数据库、DevOps 等专家角色
- ✅ **任务队列** - 优先级管理和负载均衡
- ✅ **实时监控** - WebSocket 实时推送任务状态

## 📦 项目文件

### 核心文件
- `linux_agent_multi.py` - AgentOps 服务器（推荐）⭐
- `linux_agent_multi_client.py` - AgentOps 客户端
- `linux_agent_server.py` - 单 Agent 服务器
- `linux_agent_client.py` - 单 Agent 客户端
- `linux_agent_monitor.py` - WebSocket 实时监控
- `linux_agent_local.py` - 本地命令行版本
- `linux_agent_prototype.py` - API 版本原型

### 文档
- `PROJECT_SUMMARY.md` - 项目完整总结 ⭐
- `使用案例.md` - 实际测试案例和最佳实践 ⭐ NEW
- `API_QUICKSTART.md` - API 模型快速开始 ⭐
- `MULTI_AGENT_GUIDE.md` - Multi-Agent 系统使用指南
- `BRANDING.md` - 品牌标识和使用规范
- `QUICKSTART.md` - 本地模型快速开始
- `REMOTE_DEPLOYMENT.md` - 远程部署指南
- `linux_agent_design.md` - 架构设计文档
- `CHANGELOG.md` - 更新日志

## 🚀 快速开始

### 前置要求

- Python 3.10+
- 任一 LLM API Key（DeepSeek / Grok / OpenAI / Anthropic）

### 方式 1：Multi-Agent 系统 + API 模型（推荐）⭐

```bash
# 1. 安装依赖
pip install fastapi uvicorn aiohttp websockets pydantic tabulate

# 2. 配置 API Key（选择一个）
export DEEPSEEK_API_KEY='your-deepseek-api-key'  # 推荐，性价比最高
# export GROK_API_KEY='your-grok-api-key'
# export OPENAI_API_KEY='your-openai-api-key'
# export ANTHROPIC_API_KEY='your-anthropic-api-key'

# 3. 启动 AgentOps 服务器
python linux_agent_multi.py

# 4. 查看 Agent 列表（自动创建 4 个默认 Agent）
python linux_agent_multi_client.py agent list

# 5. 提交任务
python linux_agent_multi_client.py task submit "查看系统开放端口"
```

### 方式 2：本地模型（Ollama）

```bash
# 1. 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. 下载模型
ollama pull deepseek-r1:7b

# 3. 启动 Ollama
ollama serve

# 4. 运行本地版本
python linux_agent_local.py --interactive
```

## 🎭 支持的 LLM

### API 模型（推荐）

| 提供商 | 模型 | 成本 | 特点 |
|--------|------|------|------|
| DeepSeek | deepseek-chat | $0.14/M tokens | 性价比最高 ⭐ |
| Grok | grok-beta | $5/M tokens | 高性能 |
| OpenAI | gpt-4 | $30/M tokens | 最高质量 |
| Anthropic | claude-3.5-sonnet | $3/M tokens | 复杂推理 |

### 本地模型

| 模型 | 大小 | 特点 |
|------|------|------|
| deepseek-r1:7b | ~4GB | 平衡性能和质量 |
| deepseek-r1:1.5b | ~1GB | 快速响应 |

## 🎭 Agent 角色

Multi-Agent 系统支持 6 种专业角色：

| 角色 | 图标 | 专长 | 示例任务 |
|------|------|------|----------|
| Monitor | 📊 | 系统监控、性能分析 | 查看 CPU/内存使用率 |
| Security | 🔒 | 安全审计、权限管理 | 检查登录记录 |
| Network | 🌐 | 网络诊断、端口监控 | 查看开放端口 |
| Database | 💾 | 数据库管理、备份 | 数据库备份 |
| DevOps | ⚙️ | 容器管理、CI/CD | Docker 容器管理 |
| General | 🔧 | 通用运维 | 各类运维任务 |

## 💡 使用示例

### 创建专业化 Agent（使用不同 LLM）

```bash
# 使用 DeepSeek（性价比高）
python linux_agent_multi_client.py agent create \
  "监控专家-01" monitor \
  --provider deepseek

# 使用 Grok（高性能）
python linux_agent_multi_client.py agent create \
  "安全专家-01" security \
  --provider grok

# 使用 GPT-4（最高质量）
python linux_agent_multi_client.py agent create \
  "DevOps专家-01" devops \
  --provider openai \
  --model gpt-4

# 手动指定 API Key
python linux_agent_multi_client.py agent create \
  "网络专家-01" network \
  --provider deepseek \
  --api-key "your-api-key"
```

### 提交任务并获得 AI 分析 ⭐ NEW

```bash
# 提交任务
python linux_agent_multi_client.py task submit "查看磁盘使用情况" --auto

# 查看结果（包含 AI 分析）
python linux_agent_multi_client.py task get <task_id>
```

**输出示例：**
```
============================================================
任务 ID: 4c9592fa-6783-493e-b449-65423ac3eb77
Agent: agent-001
状态: success
任务: 查看磁盘使用情况
命令: df -h

输出:
Filesystem      Size   Used  Avail Capacity
/dev/disk1s1    466Gi  15Gi  202Gi     7%   /
...

🤖 AI 分析:
结果符合预期，未发现严重问题。
关键信息：主分区（/）使用率仅7%，空间充足；
数据分区使用率53%，需关注增长趋势。
建议定期监控Data分区，若持续增长可清理缓存或迁移数据。
============================================================
```

### 提交任务

```bash
# 自动分配到合适的 Agent
python linux_agent_multi_client.py task submit "查看系统开放端口"

# 指定角色
python linux_agent_multi_client.py task submit \
  "检查最近的登录记录" \
  --role security

# 指定 Agent
python linux_agent_multi_client.py task submit \
  "查看 CPU 使用率" \
  --agent agent-001

# 高优先级任务
python linux_agent_multi_client.py task submit \
  "紧急：检查磁盘空间" \
  --priority 10 --auto
```

### 查看状态

```bash
# 查看所有 Agent
python linux_agent_multi_client.py agent list

# 查看所有任务
python linux_agent_multi_client.py task list

# 查看特定 Agent 的任务
python linux_agent_multi_client.py task list --agent agent-001

# 实时监控
python linux_agent_monitor.py
```

## 🌐 远程访问

### REST API

```bash
# 提交任务
curl -X POST http://your-server:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task": "查看系统开放端口",
    "role": "network",
    "auto_confirm": true
  }'

# 查看 Agent
curl http://your-server:8000/agents

# 查看任务
curl http://your-server:8000/tasks
```

### API 文档

启动服务器后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────┐
│                    客户端层                              │
│  CLI 客户端 | Web 界面 | API 调用 | WebSocket 监控      │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  Multi-Agent 服务器                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Agent-001│  │ Agent-002│  │ Agent-003│  ...         │
│  │ Monitor  │  │ Security │  │ Network  │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│                                                          │
│  任务队列 | 负载均衡 | 状态管理 | WebSocket 广播        │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  DeepSeek 模型层                         │
│  Ollama (本地) | DeepSeek API (云端) | vLLM (高性能)   │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   Linux 系统层                           │
│  命令执行 | 安全检查 | 结果收集 | 日志记录              │
└─────────────────────────────────────────────────────────┘
```

## 🔒 安全特性

- ✅ 危险命令黑名单（rm -rf /, dd, mkfs 等）
- ✅ 敏感命令确认机制（rm, kill, reboot 等）
- ✅ 命令执行超时保护
- ✅ Dry-run 模式（仅生成命令不执行）
- ✅ 完整的审计日志
- ✅ 任务优先级和权限控制

## 🤖 AI 反思机制 ⭐ NEW

Agent 执行完任务后会自动询问大模型分析结果，提供：

1. **结果验证** - 判断执行结果是否符合预期
2. **问题识别** - 自动发现异常和潜在问题
3. **解决方案** - 针对问题给出具体建议
4. **关键信息** - 提取和总结重要数据

这让 Agent 不仅能执行任务，还能理解和分析结果！

## 📊 特性对比

| 特性 | 本地版 | 单 Agent 服务器 | Multi-Agent 系统 |
|------|--------|----------------|------------------|
| 自然语言交互 | ✅ | ✅ | ✅ |
| 远程任务下发 | ❌ | ✅ | ✅ |
| 多 Agent 管理 | ❌ | ❌ | ✅ |
| 角色专业化 | ❌ | ❌ | ✅ |
| 任务队列 | ❌ | ✅ | ✅ |
| 负载均衡 | ❌ | ❌ | ✅ |
| 实时监控 | ❌ | ✅ | ✅ |
| REST API | ❌ | ✅ | ✅ |
| WebSocket | ❌ | ✅ | ✅ |

## 🎓 学习路径

1. **入门** - 阅读 `API_QUICKSTART.md`，配置 API Key
2. **实践** - 查看 `使用案例.md`，了解实际应用场景 ⭐
3. **进阶** - 部署 Multi-Agent 系统，管理多个专业 Agent
4. **高级** - 混合使用多个 LLM，优化成本和性能
5. **生产** - 阅读 `REMOTE_DEPLOYMENT.md`，生产环境部署

## 💰 成本优化建议

- **简单任务**：使用 DeepSeek（$0.14/M tokens）
- **复杂任务**：使用 GPT-4 或 Claude（更高准确率）
- **大规模部署**：DeepSeek 提供最佳性价比
- **本地部署**：使用 Ollama（免费但需要硬件）

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🙏 致谢

- [DeepSeek](https://www.deepseek.com/) - 提供高性价比的大模型 API
- [Grok (X.AI)](https://x.ai/) - 提供高性能的大模型 API
- [OpenAI](https://openai.com/) - 提供业界领先的大模型
- [Anthropic](https://www.anthropic.com/) - 提供 Claude 系列模型
- [Ollama](https://ollama.com/) - 简化本地模型部署
- [FastAPI](https://fastapi.tiangolo.com/) - 现代化的 Web 框架

---

**AgentOps - AI-Powered Operations, Simplified** 🚀

配置 API Key 后运行 `python linux_agent_multi.py` 开始使用！
# AgentOps
