<div align="center">

# CyberAgentOps

**AI 驱动的智能运维平台**

用自然语言管理你的服务器集群，无需记忆命令，无需手动 SSH

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Vue3](https://img.shields.io/badge/Vue-3.x-brightgreen?style=flat-square&logo=vue.js)](https://vuejs.org)

### 🌐 [https://47.111.28.162](https://47.111.28.162) — 立即体验

> 注册账号即可使用，游客也可直接进入

</div>

---

## 核心能力

### 🤖 自然语言运维

不需要记忆任何 Linux 命令。用中文描述你想做的事，AI 自动生成命令、执行、分析结果。

```
你说：查看 nginx 错误日志最近 50 行
AI做：tail -n 50 /var/log/nginx/error.log
分析：发现 3 条 502 错误，后端服务可能未启动，建议检查端口 8080 是否监听
```

支持 DeepSeek、OpenAI、Grok、Anthropic 四种 AI 模型，自动选择已配置的模型。

---

### 💬 连续对话执行

任务完成后可以继续追问，AI 始终记住完整上下文，支持连续操作：

```
查看系统进程
  → AI 分析：发现 Neo4j、Docker、nginx 正常运行

继续追问：帮我重启 nginx  [⚡ 问并执行]
  → 执行：systemctl restart nginx
  → AI 分析：nginx 已重启，服务状态正常

继续追问：查一下外网 IP  [⚡ 问并执行]
  → 执行：curl -s ifconfig.me
  → 结果：47.111.28.162
```

两种模式：
- **💬 提问** — 获取 AI 分析和建议，不执行命令
- **⚡ 问并执行** — AI 直接生成命令并在目标服务器执行

---

### 🚀 智能应用部署

填入任意 GitHub 仓库地址，系统自动完成整个部署流程：

1. **AI 分析仓库** — 识别项目类型（Python / Node.js / Java / Docker），读取依赖文件，生成部署计划，给出注意事项和建议
2. **自动安装** — 按计划执行安装步骤，兼容不同系统环境
3. **智能启动** — 推断启动命令，支持 systemd 服务注册
4. **验证结果** — 检查进程、端口、日志，AI 给出 ✅/❌ 结论和修复建议

> 如果仓库里有 `deploy.sh`，直接执行它，跳过其他步骤

支持上传配置文件（`.env` 等），可将任意格式的配置文件自动转换为 `.env` 上传到目标服务器。

---

### 📡 实时监控

Agent 部署完成后立即采集系统指标，每 10 分钟自动上报：

| 指标 | 说明 |
|------|------|
| CPU 使用率 | 实时进度条，超过 80% 红色预警 |
| 磁盘使用 | 各挂载点使用情况 |
| 网络 IO | 实时入站/出站速率（KB/s） |
| 公网 IP | 自动获取 |
| 硬件指纹 | CPU 型号、主板序列号、磁盘 ID、MAC 地址 |

---

### 🔐 权限隔离

| 角色 | 权限 |
|------|------|
| admin | 全部权限，可查看所有用户的数据 |
| 注册用户 | 管理自己的服务器和任务，数据互相隔离 |
| 游客 | 按 IP 自动分配唯一 ID，与注册用户权限相同 |

每个用户只能看到和操作自己的服务器、Agent 和任务记录。

---

### 🖥 零配置部署 Agent

添加服务器信息后，一键部署 Agent：

- 自动检测目标系统（Linux / macOS / Windows）
- 全程走 SSH，目标服务器只需开放 22 端口
- 支持密码和 SSH 密钥两种认证
- 部署完成后立即采集系统指标

---

## 架构

```
浏览器
  │
  ▼
CyberAgentOps 控制端（47.111.28.162）
  │  ├── Web UI（Vue3）
  │  ├── 用户认证 & 权限隔离
  │  ├── AI 调用（DeepSeek / OpenAI / Grok / Anthropic）
  │  └── SSH 连接管理
  │
  ▼  仅需 22 端口，无需开放其他端口
目标服务器
  └── Agent（Python 标准库，轻量无依赖）
       ├── 执行命令并返回结果
       └── 定时上报系统指标
```

---

## 实战案例

**在阿里云 ECS（Ubuntu 22.04）上的真实操作记录：**

查看系统进程 → AI 发现 Neo4j、Docker、nginx 正常运行，AliYunDun 为阿里云安全监控组件

调查安全组件 → AI 分析 AliYunDun 安装路径、systemd 配置，确认为 ECS 预装组件

连续对话查外网 IP → 执行 `curl -s ifconfig.me` → 返回 `47.111.28.162`，AI 确认网络连通正常

---

<div align="center">

**[立即访问 https://47.111.28.162](https://47.111.28.162)**

注册账号，添加你的第一台服务器，开始用自然语言管理它

Made with ❤️ — CyberAgentOps

</div>
