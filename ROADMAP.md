# 任务编排系统设计

## 目标
server 下发工作流，agent 执行技能（串行/并行），实时上报结果。

## 场景1：单 agent 工作流
server 下发工作流，单个 agent 串行/并行执行多个技能步骤。

## 场景2：多 agent 协同（分布式任务）
16 篇文章要爬取，16 台机器各爬 1 篇，互不干扰，结果汇总到 server。

```
server 拆分任务
  ├── agent-01 → 爬文章1
  ├── agent-02 → 爬文章2
  ├── ...
  └── agent-16 → 爬文章16
        ↓ 全部完成
  server 汇总结果
```

### 协议扩展
```json
// server → 多个 agent：广播分片任务
{
  "type": "task",
  "job_id": "job-abc",       // 同一批任务共享 job_id
  "task_id": "task-01",      // 每个 agent 的子任务 id
  "skill": "fetch_url",
  "params": {"url": "https://example.com/article/1"}
}

// agent → server：子任务完成
{
  "type": "task_result",
  "job_id": "job-abc",
  "task_id": "task-01",
  "agent_id": "agent-01",
  "success": true,
  "output": "..."
}
```

server 维护 job 状态，等所有子任务完成后触发汇总。

## 单 agent 工作流消息协议

### server → agent：下发工作流
```json
{
  "type": "workflow",
  "workflow_id": "wf-abc123",
  "steps": [
    {"step_id": "s1", "skill": "exec", "params": {"command": "df -h"}, "depends_on": []},
    {"step_id": "s2", "skill": "exec", "params": {"command": "free -m"}, "depends_on": []},
    {"step_id": "s3", "skill": "discover", "params": {}, "depends_on": ["s1", "s2"]}
  ]
}
```

### agent → server：每步完成实时上报
```json
{"type": "step_result", "workflow_id": "wf-abc123", "step_id": "s1", "success": true, "output": "...", "done": false}
```

### agent → server：全部完成
```json
{"type": "workflow_result", "workflow_id": "wf-abc123", "done": true, "results": {"s1": {}, "s2": {}, "s3": {}}}
```

## 技能注册表（agent 端）
```python
SKILLS = {
    "exec":      lambda self, params: self.execute_command(params["command"]),
    "discover":  lambda self, params: self.discover_apps(),
    "metrics":   lambda self, params: self.collect_metrics(),
    "fetch_url": lambda self, params: self.fetch_url(params["url"]),  # 未来
}
```
新增技能只需往字典里加，不改协议。

## 执行引擎
DAG 拓扑排序，无依赖的步骤并发执行，有依赖的等依赖完成后触发。

## 未来扩展
- LLM 直接生成 workflow/job JSON，自然语言驱动自动化
- Windows/Linux/Android 各自实现支持的技能子集
- job 级别的失败重试、超时、部分成功处理


## 应用部署多方式支持

当前只支持 GitHub 仓库部署，需要扩展以下方式：

| 部署方式 | 场景 | 示例 |
|---------|------|------|
| GitHub 仓库（现有）| 自研项目 | `git clone + pip install + python main.py` |
| 包管理器 | 官方工具 | `apt install filebeat` / `yum install nginx` |
| 二进制下载 | 无包管理器 | `wget xxx.tar.gz && tar xz && ./filebeat` |
| Docker 镜像 | 容器化应用 | `docker run -d -p 8080:80 nginx` |

前端表单加「部署方式」选择，后端 `_run_app_deploy` 根据类型走不同逻辑。
