# AgentOps 实战演示

> AI 驱动的智能运维系统 — 用自然语言管理远程服务器

---

## 系统架构

```
你的机器（控制端）                        目标服务器
┌─────────────────────┐                ┌──────────────────┐
│  AgentOps Server    │                │                  │
│  ┌───────────────┐  │   SSH 隧道     │   Ubuntu 22.04   │
│  │  FastAPI API  │◄─┼───────────────►│   任意系统       │
│  │  DeepSeek LLM │  │  仅需 22 端口  │                  │
│  └───────────────┘  │                └──────────────────┘
│                     │
│  agentops client    │
│  （CLI 工具）        │
└─────────────────────┘
```

**核心流程：**
自然语言 → DeepSeek 生成命令 → SSH 下发执行 → AI 分析结果

---

## 实战演示

### Step 1：配置目标服务器

在 `hosts.yaml` 中填写服务器信息，无需其他操作：

```yaml
hosts:
  japanhost_01:
    host: 47.111.28.162
    port: 22
    username: root
    password: ********
    deploy_dir: /opt/agentops
```

---

### Step 2：一键部署 Agent

```bash
$ python3 client.py deploy japanhost_01

正在部署到 root@47.111.28.162:22 ...
```

**系统自动完成：**
- SSH 登录目标服务器
- 检测操作系统类型和版本
- 上传 Agent 程序
- 安装依赖并启动

**实际输出：**
```
✅ 部署成功
   Agent ID : agent-bcf312af
   系统     : Ubuntu 22.04.5 LTS     ← 自动识别，无需手动指定
   目录     : /opt/agentops
   状态     : online
```

> 无需提前知道目标系统是什么，自动识别 Linux / macOS / Windows。

---

### Step 3：用自然语言下发任务

#### 任务一：查看 v2ray 日志

```bash
$ python3 client.py run "查看 v2ray server 的日志信息，包括最近的访问记录和错误信息" \
    --agent agent-bcf312af
```

**DeepSeek 自动生成的命令：**
```bash
journalctl -u v2ray -n 50 --no-pager && \
tail -n 50 /var/log/v2ray/access.log 2>/dev/null || \
tail -n 50 /var/log/v2ray/error.log 2>/dev/null
```

**执行输出：**
```
-- No entries --
```

**AI 分析结果：**
```
v2ray 服务可能未通过 systemd 管理，或日志路径不同。
建议检查 v2ray 服务状态及日志配置路径，确保服务正常运行。
```

---

#### 任务二：查看所有运行进程

```bash
$ python3 client.py run "查看当前系统运行的所有进程" --agent agent-bcf312af
```

**DeepSeek 自动生成的命令：**
```bash
ps aux
```

**执行输出（节选关键进程）：**
```
USER      PID  %CPU %MEM  COMMAND
root        1   0.0  0.3  /lib/systemd/systemd
root      796   0.0  0.2  /usr/local/aegis/aegis_update/AliYunDunUpdate
root      941   0.2  0.3  /usr/local/aegis/aegis_client/aegis_12_81/AliYunDun
root     1115   0.6  1.5  /usr/local/aegis/aegis_client/aegis_12_81/AliYunDunMonitor
root     1302   0.0  0.1  sshd: /usr/sbin/sshd -D
root    33312   0.0  0.7  /usr/bin/containerd
root    33590   0.0  0.7  /usr/bin/dockerd
neo4j   59069   0.0  3.7  /usr/bin/java ... neo4j ...
neo4j   59334   0.2 16.3  /usr/lib/jvm/java-21-openjdk-amd64/bin/java ... Neo4jCommunity
root   393885   0.0  0.2  nginx: master process
root   649816   0.0  1.8  /usr/bin/Xvfb :99
root   649899   0.0  0.4  /usr/local/bin/pinchtab server
```

**AI 分析结果：**
```
系统进程运行正常，无显著异常。
主要服务：nginx、Docker、Neo4j 图数据库、阿里云安全监控。
```

---

#### 任务三：深入调查 AliYunDun

发现 AliYunDun 进程后，继续追问：

```bash
$ python3 client.py run \
  "查看阿里云盾 AliYunDun 的安装路径、配置文件位置、以及它监听的端口和网络连接情况" \
  --agent agent-bcf312af
```

**DeepSeek 自动生成的命令：**
```bash
find / -name "*aliyun*" -type f 2>/dev/null | head -20 && \
ps aux | grep -i aliyun && \
ss -tulnp | grep -i aliyun
```

**执行输出：**
```
/usr/local/share/aliyun-assist/2.2.4.1065/aliyun-service
/usr/local/share/aliyun-assist/2.2.4.1065/log/aliyun_assist_main.log.20260401
...
/etc/systemd/system/aegis.service:ExecStart=/usr/local/aegis/aegis_update/AliYunDunUpdate
```

**AI 分析结果：**
```
AliYunDun 安装在 /usr/local/aegis/，通过 systemd aegis.service 开机自启。
这是阿里云 ECS 预装的安全监控组件，属正常现象。
aliyun-assist 是独立的 ECS 远程命令代理，两者功能不同。
```

---

## 能力总结

| 能力 | 说明 |
|------|------|
| 零配置部署 | 只需 IP + 用户名 + 密码，自动识别 OS，自动上传 Agent |
| 自然语言运维 | 不需要记命令，用中文描述需求即可 |
| AI 命令生成 | DeepSeek 根据任务和系统类型生成最合适的命令 |
| AI 结果分析 | 执行完自动分析输出，给出结论和建议 |
| 无端口要求 | 全程走 SSH 隧道，目标机器只需开放 22 端口 |
| 多系统支持 | Linux / macOS / Windows 自动适配 |
| 多服务器管理 | hosts.yaml 统一管理，按名称操作 |

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r agentops/requirements.txt

# 2. 配置 LLM Key
echo "DEEPSEEK_API_KEY=your-key" > agentops/.env

# 3. 启动控制端
python3 agentops/server/main.py

# 4. 配置目标服务器（编辑 agentops/hosts.yaml）

# 5. 一键部署
python3 agentops/client.py deploy your-server

# 6. 开始运维
python3 agentops/client.py run "查看磁盘使用情况" --agent agent-xxxxxxxx
```
