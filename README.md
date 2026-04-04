<div align="center">

# CyberAgentOps

**用自然语言控制你的每一台机器**

一个平台，管理全球任意数量的 Linux / Windows / macOS 服务器，AI 驱动，实时响应

[![Python](https://img.shields.io/badge/Python-3.6+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Vue3](https://img.shields.io/badge/Vue-3.x-brightgreen?style=flat-square&logo=vue.js)](https://vuejs.org)
[![WebSocket](https://img.shields.io/badge/WebSocket-实时通信-orange?style=flat-square)](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)

</div>

---

## 这是什么

CyberAgentOps 是一个**分布式 AI 运维平台**。

你只需要一个浏览器，就能用中文对话的方式管理散布在全球各地的服务器——不管是阿里云、AWS、自建机房，还是内网机器，都能接入。

核心思路很简单：在每台目标机器上部署一个轻量 Agent，Agent 主动连回控制端，建立持久的 WebSocket 通道。之后你说什么，它就做什么。

---

## 核心能力

### 一键部署 Agent，跨平台支持

添加服务器 SSH 信息，点击部署，系统自动完成：

- 检测操作系统（Ubuntu / CentOS / Debian / Windows Server / macOS）
- 检测 Python 环境，缺失则自动安装
- 上传 Agent 文件，安装依赖
- 启动 Agent 进程，建立 WebSocket 连接

支持 Python 3.6+，兼容 CentOS 7 这类老系统。

---

### WebSocket 反向连接，内网机器也能管

传统运维工具需要目标机器开放端口，内网机器根本无法接入。

CyberAgentOps 反过来——Agent 主动连控制端，只要目标机器能访问互联网，就能接入，无需开放任何入站端口。

断线自动重连，指数退避，网络抖动不影响使用。

---

### 自然语言执行任务

不需要记忆命令。用中文描述你想做的事：

```
查看 nginx 错误日志最近 50 行
  → tail -n 50 /var/log/nginx/error.log
  → AI 分析：发现 3 条 502 错误，后端服务可能未启动

帮我重启 nginx
  → systemctl restart nginx
  → nginx 已重启，服务状态正常

查一下磁盘使用情况
  → df -h
  → /dev/sda1 使用率 87%，建议清理 /var/log 目录
```

支持连续对话，AI 始终记住完整上下文。两种模式：
- **提问** — 获取分析和建议，不执行命令
- **问并执行** — AI 生成命令并直接在目标机器执行

---

### 多 Agent 协同（分布式任务）

这是 CyberAgentOps 区别于其他运维工具的核心能力。

你可以同时向多台机器下发任务，每台机器独立执行，结果汇总回来。

典型场景：
- 16 台机器同时爬取 16 个页面，每台机器 IP 不同，完全规避封禁
- 批量部署应用到 100 台服务器，并行执行，分钟级完成
- 多地区同时压测，模拟真实用户分布

---

### 智能应用部署

填入 GitHub 仓库地址，系统自动完成整个部署流程：

1. AI 分析仓库结构，识别项目类型（Python / Node.js / Java / Docker）
2. 自动安装依赖
3. 推断启动命令，支持 systemd 服务注册
4. 验证部署结果，AI 给出结论和修复建议

支持上传 `.env` 等配置文件，支持 `deploy.sh` 自定义部署脚本。

---

### 实时监控

Agent 连接后立即采集系统指标，定时上报：

| 指标 | 说明 |
|------|------|
| CPU 使用率 | 实时进度条，超 80% 红色预警 |
| 磁盘使用 | 各挂载点使用情况 |
| 网络 IO | 实时入站/出站速率 |
| 公网 IP | 自动获取 |
| 硬件指纹 | CPU、主板、磁盘、MAC 地址唯一标识 |

---

### 多用户权限隔离

| 角色 | 权限 |
|------|------|
| admin | 全部权限，可查看所有用户数据 |
| 注册用户 | 管理自己的服务器和任务，数据互相隔离 |
| 游客 | 按 IP 自动分配唯一 ID |

---

## 架构

```
浏览器
  │
  ▼
CyberAgentOps 控制端
  ├── Web UI（Vue3，单文件，无构建）
  ├── REST API（FastAPI）
  ├── WebSocket 连接池（管理所有 Agent 连接）
  ├── AI 调用（DeepSeek / OpenAI / Grok / Anthropic）
  └── SSH 部署引擎

  ▲  Agent 主动连接，无需开放入站端口
  │
目标机器（任意数量）
  └── Agent（Python，~50KB，无外部依赖）
       ├── WebSocket 客户端，断线自动重连
       ├── 执行命令并返回结果
       ├── 采集系统指标
       └── 支持 Linux / Windows / macOS
```

---

## 快速开始

**1. 启动控制端**

```bash
git clone https://github.com/your/cyberagentops
cd cyberagentops
pip install -r requirements.txt
cp .env.example .env  # 填入 AI API Key
cd server && python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

**2. 访问 Web 界面**

打开 `http://localhost:8000`，用 `.env` 里配置的 admin 账号登录。

**3. 添加第一台服务器**

进入「目标机器」，填入 SSH 信息，点击「保存并部署 Agent」。

部署完成后，Agent 自动上线，可以开始下发任务。

---

## 支持的平台

| 平台 | 版本 | 状态 |
|------|------|------|
| Ubuntu / Debian | 18.04+ | ✅ 完整支持 |
| CentOS / RHEL | 7+ | ✅ 完整支持（自动安装 Python3） |
| Windows Server | 2016+ | ✅ 完整支持 |
| macOS | 10.15+ | ✅ 完整支持 |
| 内网机器 | 任意 | ✅ WebSocket 反向连接 |

---

## AI 模型支持

| 模型 | 配置项 |
|------|--------|
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenAI GPT-4 | `OPENAI_API_KEY` |
| Anthropic Claude | `ANTHROPIC_API_KEY` |
| Grok | `GROK_API_KEY` |

---

<div align="center">

**用自然语言，控制你的每一台机器**

Made with ❤️ — CyberAgentOps

</div>
