<div align="center">

# CyberAgentOps

**用自然语言，掌控全球每一台设备**

一个平台，统一管理 Linux / Windows / macOS / Android，AI 驱动，跨节点协同，实时响应

[![Python](https://img.shields.io/badge/Python-3.6+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Vue3](https://img.shields.io/badge/Vue-3.x-brightgreen?style=flat-square&logo=vue.js)](https://vuejs.org)
[![Android](https://img.shields.io/badge/Android-Agent-brightgreen?style=flat-square&logo=android)](https://developer.android.com)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

[English](README_EN.md) · [快速开始](#快速开始) · [功能演示](#核心能力) · [架构设计](#架构)

</div>

---

## 这是什么

CyberAgentOps 是一个**AI 驱动的分布式设备管理平台**。

你只需要一个浏览器，就能用自然语言对话的方式，同时管理散布在全球各地的服务器、手机、PC——不管是云服务器、内网机器，还是你口袋里的 Android 手机，都能接入，都能协同。

**不需要记命令。不需要开端口。不需要 VPN。**

在每台设备上部署一个轻量 Agent，Agent 主动连回控制端，建立持久的 WebSocket 通道。之后你说什么，它就做什么。

---

## 核心能力

### 🔬 IP 纯净度检测 — 专为 TikTok 跨境电商设计

这是 CyberAgentOps 面向普通用户的公开功能，无需登录，打开即用。

**访问 `/probe`，自动完成：**

- **出口 IP 检测**：识别 IP 类型（住宅 / 机房 / 代理），给出 TikTok 适用性判断
- **WebRTC 泄露检测**：浏览器层面检测真实 IP，判断代理是否泄露
- **DNS 泄露检测**：对比本机 DNS 与 Google DoH，判断代理是否接管 DNS
- **出口分流检测**：对比访问不同目标时的出口 IP，判断代理规则是否干净
- **反向路由侦察**：用我们全球节点 traceroute 用户 IP，分析路由路径和 IP 画像
- **综合纯净度评分**：0-100 分，参考 IPPure 系数逻辑

```
用户打开 /probe
  → 自动检测出口 IP（住宅/机房/代理）
  → WebRTC 检测真实 IP（判断是否泄露）
  → DNS 泄露检测
  → 全球节点反向 traceroute 用户 IP
  → 综合评分 + 修复建议
  → 引导下载 Android App 做完整路径检测
```

---

### 📡 线路质量检测 — TikTok 直播线路分析

访问 `/netcheck-ui`，无需登录，选择节点检测目标域名：

- **实时延迟折线图**：每 3 秒刷新，200ms / 500ms 基准线
- **抖动分析**：计算 Jitter（Ping 方差），高抖动直播必卡
- **丢包检测**：红色竖条标记每次丢包
- **MTR 路由路径可视化**：每跳显示国旗 + 城市 + 运营商类型 + 延迟条
- **IP 纯净度分析**：多数据源对比（ipinfo / ip-api / db-ip），检测机房/VPN 标记
- **出口分流检测**：Cloudflare trace 对比，判断 TikTok 是否走不同出口
- **DNS 泄露检测**：agent 端解析 + Google DoH 对比

---

### 🎯 目标侦察 — 多节点并发 traceroute

从全球多个节点同时对目标域名进行 traceroute，分析目标服务器的网络画像：

- 目标服务器托管位置（城市 / 机房 / 运营商）
- 各节点到达目标的路径差异
- CDN 分布和多接入点检测
- 最优接入节点推荐

---

### 🤖 Swarm 多 Agent 协同 — 让机器集群像一个大脑一样工作

这是 CyberAgentOps 最强大的能力。

用一句自然语言描述目标，AI 自动将任务拆解，分配给最合适的节点并行执行，结果汇总成一份完整报告。

```
目标：扫描 www.baidu.com — 多节点并行端口扫描、DNS查询、服务识别

→ 香港节点：nmap 扫描 80/443 端口，curl 检测 Server 头
→ 阿里云节点：dig 查询 DNS 记录和 TTL
→ 美国节点：全端口扫描，识别非标准端口
→ 华为手机：从移动网络视角 ping 测速
→ 荣耀手机：从 WiFi 视角对比延迟

AI 汇总：baidu.com 使用自研 bfe 服务器，多 IP 负载均衡，
         香港节点延迟 11ms，移动端 25ms，美国节点 160ms
```

**Swarm 知识库**：每次成功执行的命令自动沉淀为经验，下次遇到相似任务，AI 直接复用已验证的方案，越用越聪明。

---

### 📱 Android 原生 Agent — 手机也是你的节点

手机不只是通信工具，它是一个有真实 IP、真实运营商、真实网络路径的移动节点。

- **息屏后台常驻**：WakeLock + Foreground Service，黑屏不断线
- **自动重连**：网络切换、服务器重启后自动恢复连接
- **原生网络命令**：无需 curl/traceroute，OkHttp + InetAddress 原生实现
  - `traceroute`：TTL 递增 ping，真实路由跳点
  - `ping`：系统 `/system/bin/ping`，真实 ICMP
  - `nslookup`：Java InetAddress，原生 DNS 解析
  - `curl`：OkHttp，支持 ipinfo / cloudflare trace / DoH
- **真实网络测速**：从移动端视角测试 CDN 延迟，服务器节点无法替代
- **开机自启**：设备重启后自动连回控制端

**两个 Android App：**

| App | 功能 | 包名 |
|-----|------|------|
| CyberAgent | 接受服务端命令，作为探测节点 | com.cyberagentops.agent |
| CyberNetCheck | 本机直接执行检测，展示线路质量 | com.cyberagentops.netcheck |

```
支持 Android 8.0+，无需 root，无需 Termux
```

---

### 🌐 跨节点联邦 — 本地与云端共生

本地服务器和云端服务器双向实时同步，形成一个统一的控制平面。

- 本地控制台可以操作连在云端的设备
- 云端控制台可以操作连在本地的设备
- 命令自动代理转发，对用户完全透明
- 30 秒双向同步，数据不丢失

```
本地 server ←──── 30s 双向同步 ────→ 云上 server
     ↑                                      ↑
  本地 agents                           云上 agents
  (手机/Mac)                          (Linux/Windows)
```

---

### ⚡ 自然语言执行任务

不需要记忆命令，用中文描述你想做的事：

```
查看 nginx 错误日志最近 50 行
  → tail -n 50 /var/log/nginx/error.log
  → AI 分析：发现 3 条 502 错误，后端服务可能未启动

帮我重启 nginx 并确认状态
  → systemctl restart nginx && systemctl status nginx
  → nginx 已重启，服务运行正常，监听 80/443 端口

查一下磁盘使用情况，哪个目录最大
  → df -h && du -sh /* 2>/dev/null | sort -rh | head -10
  → /var/log 占用 23G，建议清理
```

支持连续对话，AI 始终记住完整上下文。

---

### 🔗 WebSocket 反向连接 — 内网机器也能管

传统工具需要目标机器开放端口，内网机器根本无法接入。

CyberAgentOps 反过来——Agent 主动连控制端，只要能访问互联网就能接入，无需开放任何入站端口，无需 VPN，无需公网 IP。

断线自动重连，指数退避，网络抖动不影响使用。

---

### 📊 大屏实时监控

重新设计的监控界面，大屏风格，一眼看清所有节点状态：

- 顶部 4 大指标：总节点数、在线数、离线数、平均 CPU
- 在线/离线一键筛选
- 每张卡片显示：最新上报时间、CPU 使用率（渐变进度条）、内存、磁盘、网络 IO
- 离线节点显示精确离线时间和"X 分钟前"
- Agent 每 30 秒主动推送指标，数据始终最新

---

### 🚀 智能应用部署

填入 GitHub 仓库地址，系统自动完成整个部署流程：

1. AI 分析仓库结构，识别项目类型（Python / Node.js / Java / Docker）
2. 自动安装依赖
3. 推断启动命令，注册 systemd 服务
4. 验证部署结果，AI 给出结论和修复建议

---

### 🔑 USB Agent — 插上即连

将 Agent 打包到 U 盘，插入自己的任意电脑，运行启动脚本，设备自动接入控制平台。

支持 macOS / Linux / Windows 三平台，GitHub Actions 自动编译，下载即用。

---

## 真实案例

### 案例一：移动端 CDN 调度差异分析

两台手机连接不同网络，同时查询 baidu.com、taobao.com、douyin.com 的 DNS 解析 IP：

| 域名 | 华为（家庭WiFi） | 荣耀（公司网络） | 差异 |
|------|----------------|----------------|------|
| baidu.com | 111.63.65.247 | 111.63.65.247 | 相同 |
| taobao.com | 2408:4001:f00::87 | 2408:4001:f10::6f | **不同节点** |
| douyin.com | 122.14.229.58 | 122.14.229.58 | 相同 |

淘宝 CDN 对两个网络调度到了不同的 IPv6 节点——这是传统服务器监控永远看不到的真实用户视角。

### 案例二：全球 6 节点并行 ping 百度

| 节点 | 延迟 (avg) | 丢包 |
|------|-----------|------|
| 香港服务器 | 11.4ms | 0% |
| 阿里云（杭州） | 12.2ms | 0% |
| 华为手机（WiFi） | 25.1ms | 0% |
| 荣耀手机（公司网） | 27.0ms | 0% |
| 美国服务器 | 159.7ms | 0% |
| Windows 服务器 | 正常 | 0% |

6 台设备并行执行，30 秒内完成，AI 自动汇总分析报告。

---

## 快速开始

**1. 启动控制端**

```bash
git clone https://github.com/Killjat/AgentOps
cd AgentOps
pip install -r requirements.txt
cp .env.example .env  # 填入 AI API Key
python3 -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

**2. 访问 Web 界面**

- `https://www.cyberstroll.top/` — 产品介绍页（公开）
- `https://www.cyberstroll.top/probe` — IP 纯净度检测（公开，无需登录）
- `https://www.cyberstroll.top/netcheck-ui` — 线路质量检测（公开，无需登录）
- `https://www.cyberstroll.top/admin` — 控制台（需要登录）

本地开发：打开 `http://localhost:8000/admin`，用 `.env` 里配置的 admin 账号登录。

**3. 添加第一台服务器**

进入「目标机器」，填入 SSH 信息，点击「保存并部署 Agent」，30 秒内上线。

**4. 安装 Android Agent**

从 [Releases](https://github.com/Killjat/AgentOps/releases/latest) 下载最新 APK：

- `cyberagent.apk` — 作为探测节点，接受服务端命令
- `cybernetcheck.apk` — 本机检测 App，打开自动运行线路质量检测

安装后 App 自动连接 `https://47.111.28.162:8443`，无需配置。

**5. 配置双节点同步（可选）**

在 `.env` 中添加对端地址，实现本地与云端双向同步：

```env
PEER_URL=https://your-cloud-server:8443
```

---

## 支持的平台

| 平台 | 版本 | 状态 |
|------|------|------|
| Ubuntu / Debian | 18.04+ | ✅ 完整支持 |
| CentOS / RHEL | 7+ | ✅ 完整支持 |
| Windows Server | 2016+ | ✅ 完整支持 |
| macOS | 10.15+ | ✅ 完整支持 |
| Android | 8.0+ | ✅ 原生 APK，息屏常驻 |
| 内网机器 | 任意 | ✅ WebSocket 反向连接 |

---

## AI 模型支持

| 模型 | 配置项 |
|------|--------|
| DeepSeek（推荐） | `DEEPSEEK_API_KEY` |
| OpenAI GPT-4 | `OPENAI_API_KEY` |
| Anthropic Claude | `ANTHROPIC_API_KEY` |
| Grok | `GROK_API_KEY` |

---

## 架构

```
浏览器
  │
  ▼
CyberAgentOps 控制端（本地 or 云端）
  ├── Web UI（Vue3，单文件，零构建）
  ├── REST API（FastAPI）
  ├── WebSocket 连接池
  ├── Swarm 协调器（AI 规划 + 并行执行 + 知识库）
  ├── 双节点同步引擎（30s 双向同步）
  ├── AI 调用（DeepSeek / OpenAI / Claude / Grok）
  └── SQLite 持久化

  ▲  Agent 主动连接，无需开放入站端口
  │
目标设备（无限扩展）
  ├── Linux / Windows / macOS Agent（Python，~50KB）
  └── Android Agent（原生 APK，Kotlin，息屏常驻）
```

---

<div align="center">

**让每一台设备都成为你的节点**

Made with ❤️ — CyberAgentOps

[⭐ Star 支持我们](https://github.com/Killjat/AgentOps) · [🐛 提交 Issue](https://github.com/Killjat/AgentOps/issues) · [💬 讨论](https://github.com/Killjat/AgentOps/discussions)

</div>
