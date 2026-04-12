<div align="center">

# CyberStroll

**网络路径情报平台 · 独立站竞品情报 · 代理基础设施分析**

从多个真实节点对目标 IP 进行 traceroute、端口扫描、威胁情报聚合，同时提供独立站竞品情报——一键发现竞争对手的技术栈、广告投放渠道和社交媒体账号。

[![Python](https://img.shields.io/badge/Python-3.6+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Android](https://img.shields.io/badge/Android-Agent-brightgreen?style=flat-square&logo=android)](https://developer.android.com)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

[English](README_EN.md) · [快速开始](#快速开始) · [核心功能](#核心功能) · [架构设计](#节点架构)

**在线体验：https://www.cyberstroll.top**

</div>

---

## 这是什么

CyberStroll 是一个**网络情报平台**，有两条核心能力线：

**1. 网络路径情报**：从多个地理位置的真实节点（包括移动端运营商节点）对目标 IP 并发 traceroute，通过路径收敛分析识别代理基础设施，结合端口扫描和威胁情报提供完整 IP 画像。

**2. 独立站竞品情报**：输入商品关键词，自动从 FOFA 发现全球相关独立站，分析每个站的建站平台、CDN、支付方式、技术栈、社交媒体账号和广告投放情况。

---

## 核心功能

### 🏪 独立站情报 (`/ecom-intel`) ⭐ 新功能

跨境电商卖家的竞品分析工具。输入关键词，5分钟内看完所有竞争对手的完整画像。

- **批量搜索**：输入关键词（如 "phone case"），自动从 FOFA 发现相关独立站
- **单站深度分析**：直接输入域名，秒级返回完整情报
- **建站平台识别**：Shopify / WooCommerce / BigCommerce / Next.js / 自建
- **CDN 识别**：Cloudflare / CloudFront / Vercel / Netlify / Fastly
- **支付方式**：Stripe / PayPal / Klarna / Afterpay / Shop Pay
- **社交媒体账号**：自动提取 TikTok / Instagram / YouTube / Facebook 账号，一键跳转
- **广告投放检测**：识别 TikTok Pixel / Facebook Pixel，判断是否在投广告及 Pixel ID
- **价格区间**：直接抓取页面展示价格

**典型用例**：搜索 "phone case" → 发现 shakercase.com → 看到他们有 TikTok 账号 `@shakercase_com` 但没有 TikTok Pixel（有内容但没投广告）→ 这是你的切入机会。

### 🛡️ IP 纯净度检测 (`/probe`)

无需登录，打开即用。输入任意 IP 或域名，3秒内给出结论。

- **出口 IP 检测**：识别 IP 类型（住宅 / 机房 / 代理），给出 TikTok 适用性评分
- **WebRTC 泄露检测**：浏览器层面检测真实 IP，判断代理是否泄露
- **DNS 泄露检测**：对比本机 DNS 与 Google DoH，判断代理是否接管 DNS
- **多节点反向 traceroute**：从全球节点 traceroute 目标 IP，分析路由路径
- **威胁情报**：AbuseIPDB + VirusTotal 双源查询
- **综合纯净度评分**：0-100 分

### 📡 网络质量检测 (`/netcheck-ui`)

支持用户自安装 Agent，用自己的真实网络环境检测到目标站点的连接质量。

- **双模式检测**：裸连（不走代理）vs 代理链路，对比结果一目了然
- **实时延迟折线图**：每 3 秒刷新，抖动分析，丢包检测
- **路由路径可视化**：每跳显示国旗 + 城市 + 运营商 + 延迟
- **一键安装 Agent**：下载 zip 包，解压双击运行，页面自动检测到连接

### 📊 路由情报洞察 (`/insights`)

基于持续积累的 traceroute 数据，自动分析代理基础设施。

- **路径收敛检测**：识别多个目标 IP 共享同一上游网关的模式
- **代理出口网关识别**：已发现 `45.207.215.1`（cognetcloud HK）关联 287 个机场节点
- **ASN 分布分析**：AS7578 Global Secure Layer 在 1071 个跳点中出现 134 次
- **自动化调度**：FOFA 定时拉取 → scan_queue → 多节点 traceroute → 收敛分析

### 🔎 网络资产搜索 (`/portscan`)

FOFA 风格的搜索界面，查询端口扫描数据库。

```
port=8388          # 查开放 Shadowsocks 端口的所有 IP
protocol=shadowsocks
ip=61.61.69.0/24   # CIDR 网段查询
gateway=45.207.215.1
profile=full_proxy
```

### ⚡ 批量 IP 探测 (`/batch-scan`)

- FOFA 查询直接导入 IP 列表
- 实时扫描 + 后台队列两种模式
- 多节点并发，自动入库

---

## 关键发现：代理基础设施地图

通过对 819 个代理相关 IP 的多节点 traceroute 分析：

**路径收敛**：707 个 IP（86%）检测到代理链路收敛，共享同一套基础设施。

| 网关 IP | 运营商 | 关联目标数 |
|---|---|---|
| 45.207.215.1 | cognetcloud INC (HK) | 287 个 |
| 26.22.18.26 | 阿里云内网 | 116 个 |
| 26.22.16.26 | 阿里云内网 | 108 个 |

**端口扫描**（261 个 IP）：

| 端口 | 协议 | 开放比例 |
|---|---|---|
| 8388 | Shadowsocks | 93% |
| 443 | HTTPS | 91% |
| 1080 | SOCKS5 | 81% |
| 7890 | Clash | 67% |
| 10086/10808 | V2Ray/Xray | 54-68% |

---

## 节点架构

| 节点 | 类型 | 用途 |
|---|---|---|
| 美国 Linux | 境外服务器 | 境外视角 traceroute |
| 香港 Linux | 境外服务器 | 香港视角 traceroute |
| 阿里云 Linux | 国内机房 | 国内机房视角 |
| 阿里云 Windows | 国内机房 | Windows 环境测试 |
| HUAWEI NAM-AL00 | Android 移动端 | 中国移动真实路径 |
| HONOR ALI-AN00 | Android 移动端 | 中国电信真实路径 |
| Redmi 24094RAD4C | Android 移动端 | 上海移动真实路径 |

移动端节点的独特价值：运营商真实 IP，traceroute 路径反映真实用户体验，GFW 干预在移动端可见而在机房节点不可见。

---

## 快速开始

```bash
git clone https://github.com/Killjat/AgentOps
cd AgentOps
pip install -r requirements.txt
cp .env.example .env  # 填入配置
python3 server/main.py
```

**`.env` 配置**

```env
DEEPSEEK_API_KEY=your_key   # AI 分析
ABUSEIPDB_KEY=your_key      # 威胁情报
VIRUSTOTAL_KEY=your_key     # 威胁情报
FOFA_EMAIL=your_email       # IP/站点来源
FOFA_KEY=your_key
```

**功能页面**

| 页面 | 地址 | 说明 |
|---|---|---|
| 首页 | `/` | 产品介绍，IP 快速检测入口 |
| 独立站情报 | `/ecom-intel` | 竞品分析，社交媒体 + 广告投放 |
| IP 纯净度 | `/probe` | 无需登录，输入 IP 即检测 |
| 网络质量 | `/netcheck-ui` | 支持自安装 Agent |
| 路由情报 | `/insights` | 数据分析仪表盘 |
| 资产搜索 | `/portscan` | 端口扫描数据库 |
| 批量探测 | `/batch-scan` | FOFA 导入 + 批量扫描 |

**安装 Agent**

从 [Releases](https://github.com/Killjat/AgentOps/releases/latest) 下载对应平台安装包，或在 `/netcheck-ui` 页面一键下载，解压双击运行即可接入。

---

## 技术栈

- **后端**：Python 3.11 + FastAPI + SQLite
- **前端**：原生 HTML/CSS/JS（零构建依赖）
- **Agent**：Python（PyInstaller 打包）+ Kotlin（Android）
- **AI**：DeepSeek / OpenAI / Claude
- **情报源**：AbuseIPDB + VirusTotal + FOFA
- **部署**：systemd + nginx + GitHub Actions 自动编译

**支持平台**：Ubuntu / CentOS / Windows Server / macOS / Android 8.0+

---

<div align="center">

**让每一台设备都成为你的情报节点**

Made with ❤️ — CyberStroll

[⭐ Star 支持我们](https://github.com/Killjat/AgentOps) · [🐛 提交 Issue](https://github.com/Killjat/AgentOps/issues)

苏ICP备2026014083号

</div>
