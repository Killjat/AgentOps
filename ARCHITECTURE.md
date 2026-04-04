# 架构重构说明

## 概述

为了解决代码架构混乱的问题，系统进行了重构，将 Server（服务器）、Agent（代理）和 Deployment（应用部署）三层分离。

## 架构变化

### 旧架构问题

1. **概念混淆**：`hosts.yaml` 同时用于 Agent 部署和应用部署
2. **数据重复**：`RemoteHost` 和 `AgentInfo` 包含相同的连接信息
3. **依赖混乱**：应用部署必须先有 Agent，但实际上只需要 SSH 连接
4. **命名不清**：`deploy_dir` 既用于 Agent 代码目录，又用于应用代码目录

### 新架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Server 层                            │
│  管理服务器连接信息（SSH 凭证）                            │
│  文件：servers.yaml                                      │
│  模型：ServerInfo                                         │
└────────────────────┬────────────────────────────────────────┘
                     │ 引用 (server_id)
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                      Agent 层                             │
│  管理已部署的 Agent 服务（依附于 Server）                   │
│  文件：agents.json                                       │
│  模型：AgentInfo                                          │
└────────────────────┬────────────────────────────────────────┘
                     │ 可选引用
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  Deployment 层                             │
│  管理应用部署（可部署到 Server 或通过 Agent）              │
│  文件：app_deploys.json                                  │
│  模型：AppDeployResult                                   │
└─────────────────────────────────────────────────────────────┘
```

## 数据模型变化

### ServerInfo（新增）
```python
class ServerInfo(BaseModel):
    server_id: str           # 服务器唯一 ID
    name: str                # 服务器名称
    host: str                # SSH 主机地址
    port: int = 22           # SSH 端口
    username: str            # SSH 用户名
    password: Optional[str]  # SSH 密码
    ssh_key: Optional[str]   # SSH 密钥路径
    os_type: OSType          # 操作系统类型
    os_version: str          # 操作系统版本
    owner: str              # 所有者
    created_at: str          # 创建时间
    last_connected: str      # 最后连接时间
```

### AgentInfo（重构）
```python
class AgentInfo(BaseModel):
    agent_id: str                  # Agent 唯一 ID
    server_id: str                 # 引用的服务器 ID（而非重复存储连接信息）
    name: str                      # Agent 名称
    owner: str                     # 所有者
    os_type: OSType                # 操作系统类型
    os_version: str                # 操作系统版本
    device_type: DeviceType        # 设备类型
    connection_type: ConnectionType # 连接类型
    agent_deploy_dir: str          # Agent 代码部署目录（明确命名）
    agent_port: int = 9000         # Agent 服务端口
    status: AgentStatus            # 在线状态
    created_at: str               # 创建时间
    last_seen: str                # 最后心跳时间
    metrics: dict                 # 系统指标
```

### AppDeployRequest（重构）
```python
class AppDeployRequest(BaseModel):
    target_type: str              # "server" 或 "agent"
    target_id: str                # server_id 或 agent_id
    repo_url: str                # 代码仓库地址
    branch: str = "main"         # 分支
    app_deploy_dir: str = "/opt/app"  # 应用代码部署目录（明确命名）
    install_cmd: str             # 安装依赖命令
    start_cmd: str               # 启动命令
    use_systemd: bool            # 是否注册为 systemd 服务
    service_name: str            # systemd 服务名
```

### AppDeployResult（重构）
```python
class AppDeployResult(BaseModel):
    deploy_id: str               # 部署 ID
    target_type: str             # "server" 或 "agent"
    target_id: str               # server_id 或 agent_id
    owner: str                   # 所有者
    repo_url: str                # 代码仓库地址
    app_deploy_dir: str          # 应用代码部署目录
    status: AppDeployStatus      # 部署状态
    log: str                     # 部署日志
    conversation: List[dict]     # 对话记录
    created_at: str              # 创建时间
    completed_at: str            # 完成时间
```

## API 变化

### Servers API（新增）
```
GET    /servers         # 列出服务器
POST   /servers         # 添加服务器
PUT    /servers/{id}    # 更新服务器
DELETE /servers/{id}    # 删除服务器
POST   /servers/test    # 测试 SSH 连接
```

### Agents API（修改）
```
POST   /agents/deploy       # 部署 Agent（现在需要 server_id）
GET    /agents              # 列出 Agent
GET    /agents/{id}         # 获取 Agent 详情
DELETE /agents/{id}         # 删除 Agent
POST   /agents/{id}/update  # 更新 Agent
```

**部署请求变化：**
```json
// 旧格式
{
  "name": "my-agent",
  "host": "1.2.3.4",
  "port": 22,
  "username": "root",
  "password": "xxx",
  "deploy_dir": "/opt/agentops"
}

// 新格式
{
  "server_id": "server-xxx",
  "name": "my-agent"
}
```

## 数据迁移

已提供迁移脚本 `migrate_data.py`，自动完成以下迁移：

1. **hosts.yaml → servers.yaml**
   - 将所有 Host 转换为 Server
   - 自动生成 server_id

2. **agents.json**
   - 将 Agent 的 host/port/username/password 替换为 server_id
   - 将 deploy_dir 重命名为 agent_deploy_dir

3. **app_deploys.json**
   - 添加 target_type/target_id 字段
   - 将 deploy_dir 重命名为 app_deploy_dir

## 使用建议

### 添加新服务器
```bash
# 1. 先添加服务器
POST /servers
{
  "name": "生产服务器",
  "host": "1.2.3.4",
  "port": 22,
  "username": "root",
  "password": "xxx"
}

# 返回：{"server_id": "server-abc123"}

# 2. 部署 Agent
POST /agents/deploy
{
  "server_id": "server-abc123",
  "name": "生产服务器 Agent"
}
```

### 部署应用（直接通过 Server）
```bash
POST /deploy/app
{
  "target_type": "server",
  "target_id": "server-abc123",
  "repo_url": "https://github.com/user/myapp.git",
  "app_deploy_dir": "/opt/myapp"
}
```

### 部署应用（通过 Agent）
```bash
POST /deploy/app
{
  "target_type": "agent",
  "target_id": "agent-xyz456",
  "repo_url": "https://github.com/user/myapp.git",
  "app_deploy_dir": "/opt/myapp"
}
```

## 优势

1. **清晰的职责分离**：
   - Server：管理 SSH 连接
   - Agent：管理 Agent 服务
   - Deployment：管理应用部署

2. **避免数据重复**：
   - 连接信息只在 Server 中存储一次
   - Agent 通过 server_id 引用

3. **灵活的部署方式**：
   - 可以直接部署到 Server（通过 SSH）
   - 也可以通过 Agent 部署（通过 HTTP）

4. **明确的命名**：
   - `agent_deploy_dir`：Agent 代码目录
   - `app_deploy_dir`：应用代码目录

## 兼容性

- 后端代码已完全重构
- 数据迁移脚本已准备就绪
- 前端 API 调用需要相应更新
- 旧文件 `hosts.yaml` 可以备份后删除

## 后续步骤

1. ✅ 完成代码重构
2. ✅ 创建数据迁移脚本
3. ⏳ 重启服务器使新代码生效
4. ⏳ 更新前端 API 调用
5. ⏳ 清理旧文件（备份 hosts.yaml）
