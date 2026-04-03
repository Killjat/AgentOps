# AgentOps 持久化功能说明

## 概述

AgentOps 现在支持数据持久化，服务重启后不会丢失数据。

## 持久化数据

| 数据类型 | 文件路径 | 说明 |
|---------|---------|------|
| Agents | `agents.json` | 所有已部署的 Agent 信息 |
| Tasks | `tasks.json` | 任务执行历史 |
| App Deploys | `app_deploys.json` | 应用部署记录 |
| Logs | `logs/{deploy_id}.log` | 每个部署的详细日志 |
| Users | `users.json` | 用户账户信息 |

## 持久化触发时机

### 自动保存
- Agent 部署/删除时
- Agent 上报指标时
- Agent ping 检查时
- 任务创建/完成时
- 应用部署创建/完成时
- 部署对话更新时
- 服务关闭时

### 自动加载
- 服务启动时自动加载所有持久化数据

## 新增 API

### 获取部署详细日志

```bash
GET /deploy/app/{deploy_id}/log
Authorization: Bearer {token}
```

返回：
```json
{
  "log": "完整的部署日志内容...",
  "file_exists": true
}
```

## 目录结构

```
agentops/
├── agents.json              # Agent 数据（重启后保持）
├── tasks.json              # 任务数据（重启后保持）
├── app_deploys.json        # 部署数据（重启后保持）
├── logs/                   # 详细日志目录
│   ├── abc123.log          # 部署 abc123 的日志
│   └── def456.log          # 部署 def456 的日志
├── users.json              # 用户数据
└── server/main.py          # 主服务
```

## 安全注意事项

⚠️ **重要**: 以下文件包含敏感信息，不要提交到版本控制：

- `agents.json` - 包含 SSH 密码和密钥
- `users.json` - 包含用户密码哈希
- `app_deploys.json` - 可能包含部署配置

`.gitignore` 已配置自动忽略这些文件。

## 故障恢复

如果数据文件损坏：

1. 停止服务
2. 删除损坏的 JSON 文件
3. 重启服务（会使用空数据）

```bash
rm agents.json tasks.json app_deploys.json
python3 server/main.py
```

## 性能影响

- 每次数据变更都会写入磁盘
- 对于大量 Agent，可能影响性能
- 建议定期清理旧的任务和部署记录

## 未来改进

- [ ] 支持数据库后端 (PostgreSQL/SQLite)
- [ ] 数据自动压缩和归档
- [ ] 增量保存，减少 I/O
- [ ] 数据备份和恢复功能
