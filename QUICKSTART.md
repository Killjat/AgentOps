# Linux 运维 Agent 快速开始

## 🚀 三种部署方式

### 方式 1：使用 DeepSeek API（最简单）

**优点：** 无需本地部署模型，响应快
**缺点：** 需要网络，有 API 调用费用

```bash
# 1. 安装依赖
pip install requests

# 2. 获取 API Key
# 访问 https://platform.deepseek.com/ 注册并获取 API Key

# 3. 配置 API Key
export DEEPSEEK_API_KEY='your-api-key-here'

# 或者直接编辑脚本中的 DEEPSEEK_API_KEY

# 4. 运行
python linux_agent_prototype.py

# 5. 使用
👤 你: 查系统开放端口
👤 你: 查看磁盘使用情况
👤 你: 找出占用内存最多的 5 个进程
```

### 方式 2：使用 Ollama 本地模型（推荐）

**优点：** 完全本地运行，无需网络，免费
**缺点：** 需要下载模型（约 4-8GB）

```bash
# 1. 安装 Ollama
# macOS/Linux:
curl -fsSL https://ollama.com/install.sh | sh

# 2. 下载 DeepSeek 模型
ollama pull deepseek-r1:7b

# 或者使用更小的模型
ollama pull deepseek-r1:1.5b

# 3. 启动 Ollama 服务
ollama serve

# 4. 安装依赖
pip install requests

# 5. 运行 Agent
python linux_agent_local.py --interactive

# 6. 使用
👤 你: 查系统开放端口
👤 你: 查看 CPU 使用率
👤 你: 列出最近登录的用户
```

### 方式 3：使用 vLLM/LocalAI（高性能）

**优点：** 高性能推理，支持 GPU 加速
**缺点：** 配置较复杂

```bash
# 1. 安装 vLLM
pip install vllm

# 2. 启动 vLLM 服务器
python -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 0.0.0.0 \
  --port 8000

# 3. 运行 Agent
python linux_agent_local.py \
  --model-type openai-compatible \
  --model-name deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --base-url http://localhost:8000 \
  --interactive
```

## 📖 使用示例

### 基础查询

```bash
# 查看系统信息
python linux_agent_local.py "查系统开放端口"
python linux_agent_local.py "查看磁盘使用情况"
python linux_agent_local.py "查看内存使用率"
python linux_agent_local.py "查看 CPU 信息"

# 进程管理
python linux_agent_local.py "找出占用 CPU 最多的 5 个进程"
python linux_agent_local.py "查看 nginx 进程状态"
python linux_agent_local.py "列出所有 Python 进程"

# 日志查看
python linux_agent_local.py "查看系统日志最后 20 行"
python linux_agent_local.py "查看 nginx 错误日志"
python linux_agent_local.py "查找包含 error 的日志"

# 网络相关
python linux_agent_local.py "查看网络连接"
python linux_agent_local.py "测试到 google.com 的连接"
python linux_agent_local.py "查看网络接口信息"
```

### 高级用法

```bash
# Dry-run 模式（只生成命令，不执行）
python linux_agent_local.py --dry-run "重启 nginx"

# 自动执行模式（跳过确认）
python linux_agent_local.py --auto "查系统开放端口"

# 交互模式
python linux_agent_local.py --interactive

# 指定模型
python linux_agent_local.py \
  --model-name deepseek-r1:1.5b \
  "查看磁盘使用情况"
```

### 实际场景

```bash
# 场景 1：系统健康检查
python linux_agent_local.py "显示系统运行时间和负载"
python linux_agent_local.py "检查磁盘空间是否充足"
python linux_agent_local.py "查看内存使用情况"

# 场景 2：故障排查
python linux_agent_local.py "查看最近的系统错误日志"
python linux_agent_local.py "检查哪个进程占用了 80 端口"
python linux_agent_local.py "查看最近失败的登录尝试"

# 场景 3：性能分析
python linux_agent_local.py "找出占用内存最多的进程"
python linux_agent_local.py "查看磁盘 IO 情况"
python linux_agent_local.py "显示网络流量统计"

# 场景 4：安全审计
python linux_agent_local.py "列出最近 20 次登录记录"
python linux_agent_local.py "查看开放的端口"
python linux_agent_local.py "检查是否有可疑进程"
```

## 🔧 配置选项

### Ollama 配置

```bash
# 查看已安装的模型
ollama list

# 删除模型
ollama rm deepseek-r1:7b

# 查看模型信息
ollama show deepseek-r1:7b

# 自定义 Ollama 端口
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

### 模型选择建议

| 模型 | 大小 | 速度 | 质量 | 推荐场景 |
|------|------|------|------|----------|
| deepseek-r1:1.5b | ~1GB | 快 | 中 | 快速响应、简单任务 |
| deepseek-r1:7b | ~4GB | 中 | 高 | 平衡性能和质量 |
| deepseek-r1:14b | ~8GB | 慢 | 很高 | 复杂任务、高准确度 |

## 🛡️ 安全注意事项

1. **危险命令拦截**
   - `rm -rf /` 等危险命令会被自动拦截
   - 需要确认的命令会提示用户

2. **命令审查**
   - 始终检查生成的命令再执行
   - 使用 `--dry-run` 先预览命令

3. **权限控制**
   - 不要以 root 用户运行 Agent
   - 使用普通用户 + sudo（需要时）

4. **日志记录**
   - 建议记录所有执行的命令
   - 便于审计和问题排查

## 🐛 故障排除

### Ollama 连接失败

```bash
# 检查 Ollama 是否运行
ps aux | grep ollama

# 启动 Ollama
ollama serve

# 测试 Ollama
curl http://localhost:11434/api/tags
```

### 模型响应慢

```bash
# 使用更小的模型
ollama pull deepseek-r1:1.5b

# 或者使用量化版本
ollama pull deepseek-r1:7b-q4
```

### 命令执行失败

```bash
# 检查命令是否存在
which ss
which netstat

# 安装缺失的工具
# Ubuntu/Debian:
sudo apt install net-tools iproute2

# CentOS/RHEL:
sudo yum install net-tools iproute2
```

## 📈 性能优化

### 1. 使用 GPU 加速（vLLM）

```bash
# 安装 CUDA 版本的 vLLM
pip install vllm

# 启动时指定 GPU
python -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tensor-parallel-size 1
```

### 2. 缓存常用命令

```python
# 在脚本中添加缓存
COMMAND_CACHE = {
    "查系统开放端口": "ss -tuln | grep LISTEN",
    "查看磁盘使用": "df -h",
    "查看内存": "free -h"
}
```

### 3. 批量执行

```bash
# 创建任务列表
cat > tasks.txt << EOF
查系统开放端口
查看磁盘使用情况
查看内存使用率
EOF

# 批量执行
while read task; do
  python linux_agent_local.py --auto "$task"
done < tasks.txt
```

## 🔄 下一步

1. **添加更多功能**
   - 多步骤任务执行
   - 结果分析和建议
   - 定时任务调度

2. **Web 界面**
   - 使用 Flask/FastAPI 创建 Web UI
   - 支持远程访问

3. **集成监控**
   - 与 Prometheus/Grafana 集成
   - 自动化告警响应

4. **扩展到其他场景**
   - Docker 容器管理
   - Kubernetes 集群操作
   - 云服务管理（AWS/阿里云）

## 📚 相关资源

- [Ollama 官网](https://ollama.com/)
- [DeepSeek 官网](https://www.deepseek.com/)
- [vLLM 文档](https://docs.vllm.ai/)
- [Linux 命令大全](https://wangchujiang.com/linux-command/)
