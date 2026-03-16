# 本机测试指南

## ✅ 测试结果

所有系统检查已通过！

```
✓ Python 3.14.2
✓ 所有依赖已安装
✓ 所有核心文件存在
✓ 系统可以启动
```

## 🎯 测试选项

### 选项 1：本地模拟演示（无需 API Key）

```bash
# 运行本地演示
python3 demo_local.py
```

这会创建 3 个模拟 Agent 并执行测试任务，不需要真实的 API Key。

### 选项 2：使用真实 API 测试

#### 步骤 1：获取 DeepSeek API Key

1. 访问 https://platform.deepseek.com/
2. 注册账号
3. 创建 API Key
4. 复制 API Key（格式：sk-xxxxxxxx）

#### 步骤 2：配置 API Key

```bash
# 方式 A：环境变量
export DEEPSEEK_API_KEY='your-real-api-key'

# 方式 B：编辑 .env 文件
# 将 .env 文件中的 sk-test-key-replace-with-real-key 
# 替换为你的真实 API Key
```

#### 步骤 3：启动服务器

```bash
# 使用启动脚本（推荐）
./start_server.sh

# 或直接运行
python3 linux_agent_multi.py
```

服务器启动后会显示：
```
============================================================
🚀 Linux 运维 Multi-Agent 系统启动
============================================================
✅ 已创建 4 个默认 Agent (使用 DeepSeek)
📡 API 文档: http://localhost:8000/docs
🌐 WebSocket: ws://localhost:8000/ws
============================================================
```

#### 步骤 4：测试（在新终端）

```bash
# 查看 Agent 列表
python3 linux_agent_multi_client.py agent list

# 创建新 Agent
python3 linux_agent_multi_client.py agent create \
  "测试Agent" general \
  --provider deepseek

# 提交测试任务（macOS 兼容）
python3 linux_agent_multi_client.py task submit "查看当前目录"
python3 linux_agent_multi_client.py task submit "列出文件"
python3 linux_agent_multi_client.py task submit "查看系统信息"

# 查看任务列表
python3 linux_agent_multi_client.py task list

# 查看任务详情
python3 linux_agent_multi_client.py task get <task_id>
```

## 📊 macOS 兼容的测试命令

由于 macOS 和 Linux 命令有差异，建议使用以下命令测试：

```bash
# ✅ macOS 和 Linux 都支持
"查看当前目录"          # pwd
"列出文件"              # ls -la
"查看系统信息"          # uname -a
"查看当前用户"          # whoami
"查看环境变量"          # env
"查看进程"              # ps aux
"查看磁盘使用"          # df -h

# ⚠️ 仅 Linux 支持（macOS 会失败）
"查看系统开放端口"      # ss -tuln (macOS 用 netstat)
"查看内存使用率"        # free -h (macOS 用 vm_stat)
```

## 🔍 故障排查

### 问题 1：API Key 无效

```
❌ LLM API 调用失败 (401): Unauthorized
```

**解决方案：**
1. 检查 API Key 是否正确
2. 确认 API Key 有余额
3. 重新配置：`export DEEPSEEK_API_KEY='your-key'`

### 问题 2：命令不存在

```
❌ 执行失败: /bin/sh: ss: command not found
```

**解决方案：**
这是正常的，macOS 不支持某些 Linux 命令。使用 macOS 兼容的命令。

### 问题 3：端口被占用

```
ERROR: [Errno 48] Address already in use
```

**解决方案：**
```bash
# 查找占用 8000 端口的进程
lsof -i :8000

# 杀死进程
kill -9 <PID>

# 或使用其他端口
uvicorn linux_agent_multi:app --port 8001
```

## 📈 测试检查清单

- [ ] 运行 `python3 test_system.py` - 所有检查通过
- [ ] 运行 `python3 demo_local.py` - 本地演示成功
- [ ] 配置真实 API Key
- [ ] 启动服务器 - 无错误
- [ ] 访问 http://localhost:8000/docs - API 文档可访问
- [ ] 创建 Agent - 成功
- [ ] 提交任务 - 成功
- [ ] 查看任务结果 - 有输出

## 🎓 下一步

测试成功后，你可以：

1. **阅读文档**
   - `API_QUICKSTART.md` - API 使用指南
   - `MULTI_AGENT_GUIDE.md` - Multi-Agent 详细指南
   - `SUMMARY.md` - 项目总结

2. **尝试高级功能**
   - 创建不同角色的 Agent
   - 使用不同的 LLM（Grok、GPT-4）
   - 设置任务优先级
   - 实时监控（`python3 linux_agent_monitor.py`）

3. **部署到服务器**
   - 参考 `REMOTE_DEPLOYMENT.md`
   - 配置 systemd 服务
   - 设置防火墙规则

## 💡 提示

- DeepSeek API 很便宜（$0.14/M tokens），可以放心测试
- 建议先用简单命令测试，确认系统正常工作
- 查看 API 文档了解所有功能：http://localhost:8000/docs
- 使用 `--help` 查看命令帮助

---

**测试愉快！** 🚀
