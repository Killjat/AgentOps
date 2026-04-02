<div align="center">

# CyberAgentOps

**AI 驱动的智能运维平台**

用自然语言管理你的服务器集群，无需记忆命令，无需手动 SSH

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Vue3](https://img.shields.io/badge/Vue-3.x-brightgreen?style=flat-square&logo=vue.js)](https://vuejs.org)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## 核心能力

### AI 对话执行

任务执行完成后，可以继续与 AI 对话，基于已有的执行结果进行追问或执行新操作：

- 「💬 提问」— 向 AI 提问，获取分析和建议，不执行命令
- 「⚡ 问并执行」— AI 直接生成命令并在目标机器执行，返回结果和分析

对话示例：

```
任务：查看系统进程
AI 分析：发现 Neo4j、Docker、nginx 正常运行...

用户：查一下外网 IP
⚡ 问并执行 → 执行命令：curl -s ifconfig.me
结果：47.111.28.162
AI 分析：服务器公网 IP 为 47.111.28.162，网络连通正常...

用户：帮我重启 nginx
⚡ 问并执行 → 执行命令：systemctl restart nginx
结果：执行成功
AI 分析：nginx 已重启，建议验证服务状态...
```

AI 始终保留完整对话上下文，可以连续追问和操作。

---



不需要记忆 Linux 命令，用中文描述你想做的事，AI 自动生成并执行对应命令，执行完还会给出结果分析。

```
你：查看 nginx 错误日志最近 50 行
AI：tail -n 50 /var/log/nginx/error.log
结果：[日志内容]
分析：发现 3 条 502 错误，建议检查后端服务状态...
```

### 零配置部署

在配置文件里填写服务器信息，一键部署 Agent 到目标机器。系统自动识别操作系统（Linux / macOS / Windows），无需提前知道目标系统环境。

### 实时监控

Agent 部署完成后立即采集系统指标，并每 10 分钟自动上报：

- CPU 使用率（实时进度条）
- 磁盘使用情况
- 网络 IO（入站/出站 KB/s）
- 公网 IP / 内网 IP
- 硬件指纹（CPU 型号、主板序列号、磁盘 ID、MAC 地址）

### 权限管理

- `admin` 拥有全部权限
- 普通用户注册后默认只读
- admin 可按需授权「执行任务」或「管理机器」权限
- 游客可浏览监控和机器信息，无法执行任何操作

---

## 系统架构

```
你的浏览器
    │
    ▼
CyberAgentOps 控制端（FastAPI）
    │  ├── Web UI（Vue3）
    │  ├── 用户认证 & 权限管理
    │  ├── LLM 调用（DeepSeek / OpenAI / Grok / Anthropic）
    │  └── SSH 连接管理
    │
    ▼（SSH，仅需 22 端口）
目标服务器
    └── Agent（Python 标准库，轻量无依赖）
         ├── 执行命令
         └── 定时上报系统指标
```

目标服务器无需开放任何额外端口，全程走 SSH 通信。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `.env`：

```env
DEEPSEEK_API_KEY=your-api-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-password
```

编辑 `hosts.yaml` 添加目标服务器：

```yaml
hosts:
  my-server:
    host: 192.168.1.100
    port: 22
    username: root
    password: yourpassword
    deploy_dir: /opt/agentops
```

### 3. 启动

```bash
python3 server/main.py
```

浏览器打开 `http://localhost:8000`

### 4. 部署 Agent

在「目标机器」页面点击「🚀 保存并部署 Agent」，系统自动完成：

1. SSH 连接目标服务器
2. 检测操作系统
3. 上传 Agent 程序
4. 启动并立即采集系统指标

### 5. 执行任务

在「任务执行」页面选择目标 Agent，用自然语言描述任务，点击执行。

---

## 支持的 LLM

| 提供商 | 模型 | 特点 |
|--------|------|------|
| DeepSeek | deepseek-chat | 推荐，性价比最高 |
| OpenAI | gpt-4o | 最高质量 |
| Grok | grok-beta | 高性能 |
| Anthropic | claude-3-5-sonnet | 复杂推理 |

---

## 项目结构

```
.
├── server/
│   ├── main.py        # FastAPI 控制端
│   ├── deployer.py    # SSH 自动部署
│   ├── llm.py         # LLM 调用封装
│   └── models.py      # 数据模型
├── agent/
│   └── agent.py       # 目标机器 Agent（纯标准库）
├── web/
│   └── index.html     # Web 界面（Vue3 CDN）
├── hosts.yaml         # 目标服务器配置
├── .env               # 环境变量
└── requirements.txt
```

---

## 实战案例

部署到阿里云 ECS（Ubuntu 22.04），执行自然语言任务：

**查看系统进程**
> 输入：查看当前系统运行的所有进程
> 生成：`ps aux`
> AI 分析：发现 Neo4j、Docker、nginx 等服务正常运行，AliYunDun 为阿里云安全监控组件，属正常现象。

**调查安全组件**
> 输入：查看阿里云盾 AliYunDun 的安装路径和网络连接情况
> 生成：`find / -name "*aliyun*" -type f 2>/dev/null | head -20 && ps aux | grep -i aliyun`
> AI 分析：AliYunDun 安装在 /usr/local/aegis/，通过 systemd 开机自启，为 ECS 预装安全组件。

**AI 对话执行（连续操作）**
> 任务：查看系统进程
> 用户追问：查一下外网 IP（⚡ 问并执行）
> AI 生成命令：`curl -s ifconfig.me`
> 执行结果：`47.111.28.162`
> AI 分析：服务器公网 IP 为 47.111.28.162，网络出站连接正常，ifconfig.me 服务响应正常。

---

<div align="center">

Made with ❤️ — CyberAgentOps

</div>
