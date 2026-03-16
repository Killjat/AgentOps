# Linux 服务器运维 Agent 设计方案

## 架构概览

```
用户指令 → Agent → DeepSeek API → 生成命令 → 执行 → 返回结果
```

## 方案 A：轻量级 Python Agent（推荐）

### 技术栈
- Python 3.10+
- DeepSeek API / 本地 DeepSeek 模型
- Click（CLI 框架）
- subprocess（命令执行）
- rich（美化输出）

### 核心组件

1. **Agent 主程序** (`linux_agent.py`)
   - 接收用户指令
   - 调用 DeepSeek 生成命令
   - 执行命令并返回结果
   - 安全检查机制

2. **DeepSeek 集成** (`llm_client.py`)
   - API 调用封装
   - Prompt 工程
   - 命令生成和解析

3. **命令执行器** (`executor.py`)
   - 安全的命令执行
   - 权限检查
   - 结果格式化

4. **安全模块** (`security.py`)
   - 危险命令黑名单
   - 用户确认机制
   - 日志记录

### 目录结构

```
linux-agent/
├── linux_agent/
│   ├── __init__.py
│   ├── cli.py              # CLI 入口
│   ├── agent.py            # Agent 核心逻辑
│   ├── llm_client.py       # DeepSeek 客户端
│   ├── executor.py         # 命令执行器
│   ├── security.py         # 安全检查
│   └── prompts.py          # Prompt 模板
├── tests/
│   ├── test_agent.py
│   ├── test_executor.py
│   └── test_security.py
├── config.yaml             # 配置文件
├── setup.py
└── README.md
```

## 方案 B：基于现有框架

### 选项 1：LangChain + DeepSeek
- 使用 LangChain 的 Agent 框架
- 集成 DeepSeek 作为 LLM
- 自定义 Shell Tool

### 选项 2：AutoGPT 风格
- 循环执行：思考 → 行动 → 观察
- 支持多步骤任务
- 记忆和上下文管理

### 选项 3：基于 CLI-Anything 思路
- 为常用运维工具生成 CLI
- DeepSeek 调用这些 CLI
- 结构化输出

## 核心功能设计

### 1. 命令生成 Prompt

```python
SYSTEM_PROMPT = """
你是一个 Linux 服务器运维专家助手。
用户会用自然语言描述需求，你需要生成对应的 Linux 命令。

规则：
1. 只返回可执行的命令，不要解释
2. 如果需要多个命令，用 && 连接
3. 优先使用安全的命令
4. 如果任务不明确，返回 "NEED_CLARIFICATION: <问题>"

示例：
用户：查看系统开放端口
助手：ss -tuln | grep LISTEN

用户：查看 CPU 使用率
助手：top -bn1 | grep "Cpu(s)"

用户：查找占用内存最多的进程
助手：ps aux --sort=-%mem | head -n 10
"""
```

### 2. 安全机制

```python
# 危险命令黑名单
DANGEROUS_COMMANDS = [
    'rm -rf /',
    'dd if=/dev/zero',
    'mkfs.',
    ':(){ :|:& };:',  # fork bomb
    'chmod -R 777 /',
    'chown -R',
]

# 需要确认的命令
CONFIRM_REQUIRED = [
    'rm', 'rmdir', 'kill', 'pkill', 
    'reboot', 'shutdown', 'halt',
    'iptables', 'ufw', 'firewall-cmd',
]
```

### 3. 执行流程

```
1. 用户输入：查系统开放端口
   ↓
2. Agent 调用 DeepSeek
   Prompt: "生成 Linux 命令：查系统开放端口"
   ↓
3. DeepSeek 返回：ss -tuln | grep LISTEN
   ↓
4. 安全检查：通过
   ↓
5. 执行命令
   ↓
6. 格式化输出：
   Proto  Local Address    State
   tcp    0.0.0.0:22       LISTEN
   tcp    0.0.0.0:80       LISTEN
```

## 使用示例

### 基础用法

```bash
# 安装
pip install linux-agent

# 配置 DeepSeek API
linux-agent config set --api-key YOUR_API_KEY

# 使用
linux-agent "查系统开放端口"
linux-agent "查看磁盘使用情况"
linux-agent "找出占用 CPU 最多的 5 个进程"

# 交互模式
linux-agent interactive
> 查系统开放端口
> 查看 nginx 日志最后 20 行
> exit
```

### 高级用法

```bash
# 自动执行（跳过确认）
linux-agent --auto "查系统开放端口"

# 仅生成命令，不执行
linux-agent --dry-run "重启 nginx"

# 详细模式（显示思考过程）
linux-agent --verbose "优化系统性能"

# 保存历史
linux-agent --save-history "系统健康检查"
```

## 扩展功能

### 1. 预定义任务模板

```yaml
# config.yaml
templates:
  health_check:
    description: "系统健康检查"
    commands:
      - "uptime"
      - "df -h"
      - "free -h"
      - "ss -tuln | grep LISTEN"
      
  security_audit:
    description: "安全审计"
    commands:
      - "last -n 20"
      - "grep 'Failed password' /var/log/auth.log | tail -20"
      - "netstat -tuln"
```

### 2. 多步骤任务

```bash
linux-agent "检查 nginx 状态，如果没运行就启动它"

# Agent 执行：
# 1. systemctl status nginx
# 2. 判断结果
# 3. 如果未运行：systemctl start nginx
```

### 3. 结果分析

```bash
linux-agent "分析系统性能瓶颈"

# Agent 会：
# 1. 检查 CPU、内存、磁盘、网络
# 2. 分析结果
# 3. 给出优化建议
```

## 安全考虑

1. **命令白名单模式**（可选）
   - 只允许预定义的安全命令
   
2. **沙箱执行**
   - 使用 Docker 容器隔离
   
3. **审计日志**
   - 记录所有执行的命令
   
4. **权限控制**
   - 限制 sudo 命令
   - 用户角色管理

5. **命令验证**
   - 语法检查
   - 参数验证

## 部署方案

### 单机部署
```bash
# 直接安装
pip install linux-agent
linux-agent config init
```

### 服务化部署
```bash
# systemd service
sudo systemctl enable linux-agent
sudo systemctl start linux-agent

# 通过 API 调用
curl -X POST http://localhost:8080/execute \
  -d '{"task": "查系统开放端口"}'
```

### Docker 部署
```dockerfile
FROM python:3.10-slim
COPY . /app
RUN pip install -e /app
CMD ["linux-agent", "serve"]
```

## 性能优化

1. **缓存常用命令**
   - 相同问题直接返回缓存的命令
   
2. **本地模型**
   - 使用本地 DeepSeek 模型减少延迟
   
3. **批量执行**
   - 合并多个相关命令

## 对比其他方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| 纯 Shell 脚本 | 快速、无依赖 | 不够智能、难维护 |
| Ansible | 成熟、功能强大 | 学习曲线陡、配置复杂 |
| 本方案 | 自然语言、灵活 | 需要 LLM、有安全风险 |

## 下一步

1. 实现基础 Agent 框架
2. 集成 DeepSeek API
3. 添加安全检查
4. 编写测试用例
5. 优化 Prompt
6. 添加更多功能
