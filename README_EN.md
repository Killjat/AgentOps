<div align="center">

# CyberAgentOps

**Control Every Device with Natural Language**

One platform to rule them all — Linux / Windows / macOS / Android, AI-powered, cross-node collaboration, real-time response

[![Python](https://img.shields.io/badge/Python-3.6+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Vue3](https://img.shields.io/badge/Vue-3.x-brightgreen?style=flat-square&logo=vue.js)](https://vuejs.org)
[![Android](https://img.shields.io/badge/Android-Agent-brightgreen?style=flat-square&logo=android)](https://developer.android.com)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

[中文](README.md) · [Quick Start](#quick-start) · [Features](#core-features) · [Architecture](#architecture)

</div>

---

## What is this?

CyberAgentOps is an **AI-driven distributed device management platform**.

With just a browser, you can manage servers, phones, and PCs scattered across the globe using natural language — whether they're on Alibaba Cloud, AWS, your home lab, or behind a corporate firewall. If it can reach the internet, it can be controlled.

**No commands to memorize. No ports to open. No VPN required.**

Deploy a lightweight Agent on each device. The Agent connects back to the control server, establishing a persistent WebSocket channel. Then just tell it what to do.

---

## Core Features

### 🤖 Swarm Multi-Agent Collaboration — Your Device Fleet, One Brain

This is CyberAgentOps' most powerful capability.

Describe your goal in plain language. AI automatically breaks it down into subtasks, assigns them to the most suitable nodes for parallel execution, and aggregates the results into a comprehensive report.

```
Goal: Scan www.baidu.com — multi-node parallel port scan, DNS query, service identification

→ Hong Kong node:    nmap scan ports 80/443, curl detect Server header
→ Alibaba Cloud:     dig DNS records and TTL
→ US node:           full port scan, identify non-standard ports
→ Huawei phone:      ping latency from mobile network perspective
→ Honor phone:       compare latency from WiFi perspective

AI Summary: baidu.com uses in-house bfe server, multi-IP load balancing.
            HK node: 11ms, mobile: 25ms, US node: 160ms
```

**Swarm Knowledge Base**: Every successful command execution is automatically recorded as institutional knowledge. Next time a similar task comes up, AI reuses proven solutions — it gets smarter with every run.

---

### 📱 Android Native Agent — Your Phone is a Node

Your phone isn't just a communication device. It's a mobile node with a real IP, real carrier, and real network path.

- **Background persistence**: WakeLock + Foreground Service — stays connected even when the screen is off
- **Auto-reconnect**: Automatically recovers after network switches or server restarts
- **Real-world network testing**: Test CDN latency from a mobile perspective — something server nodes can never replicate
- **Capability discovery**: Automatically detects available tools (ping, curl, ip, getprop, etc.)
- **Boot autostart**: Reconnects to the control server after device reboot

```
Supports Android 8.0+. No root required. No Termux needed.
```

---

### 🌐 Cross-Node Federation — Local and Cloud, United

Local and cloud servers sync bidirectionally in real time, forming a unified control plane.

- Control devices connected to the cloud from your local console
- Control devices connected locally from the cloud console
- Commands are automatically proxied — completely transparent to users
- 30-second bidirectional sync, zero data loss

```
Local server ←──── 30s bidirectional sync ────→ Cloud server
      ↑                                                ↑
  Local agents                                  Cloud agents
  (phones/Mac)                               (Linux/Windows)
```

---

### ⚡ Natural Language Task Execution

No command memorization needed. Just describe what you want:

```
Show the last 50 lines of nginx error logs
  → tail -n 50 /var/log/nginx/error.log
  → AI: Found 3 x 502 errors, backend service may be down

Restart nginx and confirm status
  → systemctl restart nginx && systemctl status nginx
  → nginx restarted successfully, listening on ports 80/443

Check disk usage, which directory is largest
  → df -h && du -sh /* 2>/dev/null | sort -rh | head -10
  → /var/log is using 23G, recommend cleanup
```

Supports continuous conversation — AI always remembers the full context.

---

### 🔗 WebSocket Reverse Connection — Manage Anything, Anywhere

Traditional tools require target machines to open inbound ports. Machines behind NAT or firewalls are simply unreachable.

CyberAgentOps flips the model — the Agent connects *out* to the control server. As long as the device can reach the internet, it can be managed. No inbound ports. No VPN. No public IP required.

Auto-reconnect with exponential backoff. Network hiccups don't break your workflow.

---

### 📊 Big-Screen Real-Time Monitoring

Redesigned monitoring dashboard with a command-center aesthetic:

- Top 4 metrics: total nodes, online, offline, average CPU
- One-click filter: ALL / ONLINE / OFFLINE
- Per-card display: last report time, CPU usage (gradient progress bar), memory, disk, network I/O
- Offline nodes show exact disconnect time and "X minutes ago"
- Agents push metrics every 30 seconds — always fresh data

---

### 🚀 Intelligent Application Deployment

Paste a GitHub repo URL. The system handles the rest:

1. AI analyzes repo structure, identifies project type (Python / Node.js / Java / Docker)
2. Automatically installs dependencies
3. Infers startup command, registers systemd service
4. Validates deployment, AI provides conclusions and fix suggestions

---

### 🔑 USB Agent — Plug In and Connect

Package the Agent onto a USB drive. Plug it into any of your machines, run the launcher script, and the device automatically joins your control platform.

Supports macOS / Linux / Windows. GitHub Actions auto-builds all three platforms — download and use.

---

## Real-World Cases

### Case 1: Mobile CDN Routing Analysis

Two phones on different networks simultaneously queried DNS for baidu.com, taobao.com, and douyin.com:

| Domain | Huawei (Home WiFi) | Honor (Office Network) | Difference |
|--------|-------------------|----------------------|------------|
| baidu.com | 111.63.65.247 | 111.63.65.247 | Same |
| taobao.com | 2408:4001:f00::87 | 2408:4001:f10::6f | **Different nodes** |
| douyin.com | 122.14.229.58 | 122.14.229.58 | Same |

Taobao's CDN routed the two networks to different IPv6 nodes — a real-user perspective that traditional server monitoring can never capture.

### Case 2: Global 6-Node Parallel Ping

| Node | Avg Latency | Packet Loss |
|------|------------|-------------|
| Hong Kong server | 11.4ms | 0% |
| Alibaba Cloud (Hangzhou) | 12.2ms | 0% |
| Huawei phone (WiFi) | 25.1ms | 0% |
| Honor phone (Office) | 27.0ms | 0% |
| US server | 159.7ms | 0% |
| Windows server | Normal | 0% |

6 devices executed in parallel, completed in under 30 seconds, AI auto-generated the analysis report.

---

## Quick Start

**1. Start the control server**

```bash
git clone https://github.com/Killjat/AgentOps
cd AgentOps
pip install -r requirements.txt
cp .env.example .env  # Add your AI API key
python3 -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

**2. Open the web interface**

Navigate to `http://localhost:8000` and log in with the admin credentials from `.env`.

**3. Add your first server**

Go to "Target Machines", enter SSH credentials, click "Save & Deploy Agent". Online in 30 seconds.

**4. Install Android Agent**

Download the latest APK from [Releases](https://github.com/Killjat/AgentOps/releases/latest), install it, and enter your control server address.

**5. Configure dual-node sync (optional)**

Add the peer address to `.env` for bidirectional sync between local and cloud:

```env
PEER_URL=https://your-cloud-server:8443
```

---

## Supported Platforms

| Platform | Version | Status |
|----------|---------|--------|
| Ubuntu / Debian | 18.04+ | ✅ Full support |
| CentOS / RHEL | 7+ | ✅ Full support |
| Windows Server | 2016+ | ✅ Full support |
| macOS | 10.15+ | ✅ Full support |
| Android | 8.0+ | ✅ Native APK, background persistence |
| Behind NAT/Firewall | Any | ✅ WebSocket reverse connection |

---

## AI Model Support

| Model | Config Key |
|-------|-----------|
| DeepSeek (recommended) | `DEEPSEEK_API_KEY` |
| OpenAI GPT-4 | `OPENAI_API_KEY` |
| Anthropic Claude | `ANTHROPIC_API_KEY` |
| Grok | `GROK_API_KEY` |

---

## Architecture

```
Browser
  │
  ▼
CyberAgentOps Control Server (local or cloud)
  ├── Web UI (Vue3, single-file, zero build)
  ├── REST API (FastAPI)
  ├── WebSocket connection pool
  ├── Swarm coordinator (AI planning + parallel execution + knowledge base)
  ├── Dual-node sync engine (30s bidirectional sync)
  ├── AI integration (DeepSeek / OpenAI / Claude / Grok)
  └── SQLite persistence

  ▲  Agents connect outbound — no inbound ports required
  │
Target devices (unlimited scale)
  ├── Linux / Windows / macOS Agent (Python, ~50KB)
  └── Android Agent (native APK, Kotlin, background persistence)
```

---

<div align="center">

**Turn every device into your node**

Made with ❤️ — CyberAgentOps

[⭐ Star us on GitHub](https://github.com/Killjat/AgentOps) · [🐛 Report Issues](https://github.com/Killjat/AgentOps/issues) · [💬 Discussions](https://github.com/Killjat/AgentOps/discussions)

</div>
