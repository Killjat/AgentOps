# Multi-Agent 系统使用指南

## 🎯 系统概述

Multi-Agent 系统允许你创建多个专业化的 Agent，每个 Agent 有自己的：
- **编号**：唯一标识（agent-001, agent-002...）
- **角色**：专业领域（监控、安全、网络等）
- **能力**：擅长的任务类型
- **状态**：在线、离线、忙碌、错误

## 🚀 快速开始

### 1. 启动服务器

```bash
# 安装依赖
pip install fastapi uvicorn aiohttp websockets pydantic tabulate

# 启动 Ollama
ollama serve

# 启动 Multi-Agent 服务器
python linux_agent_multi.py
```

服务器会自动创建 4 个默认 Agent：
- agent-001: 监控专家
- agent-002: 安全专家
- agent-003: 网络专家
- agent-004: 通用运维

### 2. 查看所有 Agent

```bash
python linux_agent_multi_client.py agent list
```

输出示例：
```
┌────────────┬──────────────────┬──────────┬────────────┬──────────────┬──────────┐
│ ID         │ 名称             │ 角色     │ 状态       │ 当前/最大任务│ 已完成   │
├────────────┼──────────────────┼──────────┼────────────┼──────────────┼──────────┤
│ agent-001  │ 📊 监控专家-01   │ monitor  │ 🟢 online  │ 0/3          │ 0        │
│ agent-002  │ 🔒 安全专家-01   │ security │ 🟢 online  │ 0/3          │ 0        │
│ agent-003  │ 🌐 网络专家-01   │ network  │ 🟢 online  │ 0/3          │ 0        │
│ agent-004  │ 🔧 通用运维-01   │ general  │ 🟢 online  │ 0/3          │ 0        │
└────────────┴──────────────────┴──────────┴────────────┴──────────────┴──────────┘
```

## 📋 Agent 角色说明

### 1. Monitor（监控专家）📊
- **专长**：系统性能监控、资源使用分析、进程管理、日志分析
- **常用工具**：top, htop, ps, free, df, iostat, vmstat, sar
- **适用任务**：
  - 查看系统资源使用情况
  - 分析性能瓶颈
  - 监控进程状态
  - 查看系统日志

### 2. Security（安全专家）🔒
- **专长**：安全审计、权限管理、防火墙配置、入侵检测
- **常用工具**：iptables, ufw, fail2ban, last, who, grep auth.log
- **适用任务**：
  - 检查登录记录
  - 配置防火墙规则
  - 审计用户权限
  - 查找安全漏洞

### 3. Network（网络专家）🌐
- **专长**：网络诊断、连接管理、端口监控、流量分析
- **常用工具**：ss, netstat, tcpdump, ping, traceroute, nmap
- **适用任务**：
  - 查看开放端口
  - 诊断网络连接
  - 分析网络流量
  - 测试网络延迟

### 4. Database（数据库专家）💾
- **专长**：数据库管理、备份恢复、性能优化、查询分析
- **常用工具**：mysql, psql, mongosh, redis-cli
- **适用任务**：
  - 数据库备份
  - 性能优化
  - 查询分析
  - 数据恢复

### 5. DevOps（DevOps 专家）⚙️
- **专长**：容器管理、CI/CD、服务部署、自动化运维
- **常用工具**：docker, kubectl, systemctl, git, ansible
- **适用任务**：
  - 容器管理
  - 服务部署
  - 自动化脚本
  - CI/CD 流程

### 6. General（通用运维）🔧
- **专长**：全面的系统管理和问题排查
- **适用任务**：各类通用运维任务

## 💼 使用场景

### 场景 1：创建新 Agent

```bash
# 创建一个数据库专家
python linux_agent_multi_client.py agent create \
  "数据库专家-01" database \
  --desc "MySQL 和 PostgreSQL 管理" \
  --model deepseek-r1:7b
```

### 场景 2：提交任务（自动分配）

```bash
# 系统会自动选择合适的 Agent
python linux_agent_multi_client.py task submit "查看系统开放端口"
```

系统会自动选择 network 角色的 Agent 执行。

### 场景 3：指定 Agent 执行任务

```bash
# 指定特定 Agent
python linux_agent_multi_client.py task submit \
  "查看 CPU 使用率" \
  --agent agent-001

# 指定角色（自动选择该角色的可用 Agent）
python linux_agent_multi_client.py task submit \
  "检查最近的登录记录" \
  --role security
```

### 场景 4：高优先级任务

```bash
# 紧急任务，优先级 10
python linux_agent_multi_client.py task submit \
  "检查磁盘空间" \
  --priority 10 \
  --auto
```

### 场景 5：查看任务状态

```bash
# 查看所有任务
python linux_agent_multi_client.py task list

# 查看特定 Agent 的任务
python linux_agent_multi_client.py task list --agent agent-001

# 查看运行中的任务
python linux_agent_multi_client.py task list --status running

# 查看任务详情
python linux_agent_multi_client.py task get <task_id>
```

### 场景 6：查看 Agent 详情

```bash
python linux_agent_multi_client.py agent get agent-001
```

输出：
```
============================================================
Agent ID: agent-001
名称: 监控专家-01
角色: monitor
状态: online
描述: 系统监控和性能分析
模型: deepseek-r1:7b
当前任务: 0/3
已完成任务: 15
能力: 系统监控, 性能分析, 资源管理, 日志分析
创建时间: 2026-03-17T10:30:00
最后活跃: 2026-03-17T11:45:23
============================================================
```

## 🔄 实时监控

使用 WebSocket 客户端实时监控所有 Agent 和任务：

```bash
python linux_agent_monitor.py ws://localhost:8000/ws
```

你会看到：
- Agent 创建/删除事件
- 任务状态变化
- 命令执行结果
- 实时输出

## 🌐 远程访问

### 通过 REST API

```bash
# 从任何机器提交任务
curl -X POST http://your-server:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task": "查看系统开放端口",
    "role": "network",
    "auto_confirm": true
  }'

# 查看 Agent 列表
curl http://your-server:8000/agents

# 查看任务列表
curl http://your-server:8000/tasks
```

### 通过客户端

```bash
# 连接到远程服务器
python linux_agent_multi_client.py \
  --server http://your-server:8000 \
  agent list
```

## 📊 API 文档

启动服务器后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🔒 安全建议

1. **生产环境部署**
   - 使用 HTTPS
   - 添加身份认证
   - 限制 API 访问

2. **Agent 权限控制**
   - 不同 Agent 使用不同系统用户
   - 限制危险命令
   - 启用审计日志

3. **网络安全**
   - 使用防火墙限制访问
   - VPN 或 SSH 隧道
   - 定期更新依赖

## 🎯 最佳实践

1. **合理分配角色**
   - 根据任务类型选择合适的 Agent
   - 避免所有任务都用 general 角色

2. **负载均衡**
   - 创建多个相同角色的 Agent
   - 系统会自动分配到负载最低的 Agent

3. **优先级管理**
   - 紧急任务使用高优先级
   - 批量任务使用低优先级

4. **监控和告警**
   - 使用 WebSocket 实时监控
   - 集成到监控系统（Prometheus/Grafana）

## 🔧 故障排除

### Agent 状态为 ERROR

```bash
# 查看 Agent 详情
python linux_agent_multi_client.py agent get <agent_id>

# 删除并重新创建
python linux_agent_multi_client.py agent delete <agent_id>
python linux_agent_multi_client.py agent create "新Agent" <role>
```

### 任务一直 PENDING

```bash
# 检查是否有可用 Agent
python linux_agent_multi_client.py agent list --status online

# 检查 Agent 是否达到最大并发
python linux_agent_multi_client.py agent list
```

### 无法连接到服务器

```bash
# 检查服务器是否运行
curl http://localhost:8000/

# 检查防火墙
sudo ufw status

# 查看服务器日志
tail -f agent.log
```

## 📈 扩展功能

### 1. 添加自定义角色

编辑 `linux_agent_multi.py`，在 `ROLE_PROMPTS` 中添加新角色。

### 2. 集成监控系统

使用 WebSocket 或 REST API 集成到 Prometheus、Grafana 等。

### 3. 多服务器管理

在每台服务器上部署 Agent 系统，使用统一的客户端管理。

## 🎓 示例工作流

### 完整的系统健康检查

```bash
# 1. 监控专家检查系统资源
python linux_agent_multi_client.py task submit \
  "查看 CPU、内存、磁盘使用情况" \
  --role monitor --priority 5

# 2. 网络专家检查网络状态
python linux_agent_multi_client.py task submit \
  "查看所有网络连接和开放端口" \
  --role network --priority 5

# 3. 安全专家检查登录记录
python linux_agent_multi_client.py task submit \
  "查看最近 24 小时的登录记录" \
  --role security --priority 5

# 4. 查看所有任务结果
python linux_agent_multi_client.py task list --limit 10
```

这就是 Multi-Agent 系统的完整使用指南！
