#!/bin/bash
# 测试 Agent 上传和执行

HOST="165.154.235.9"
USER="root"
PASS="kqvfhpsiq@099211"
DEPLOY_DIR="/opt/agentops"
AGENT_DIR="$DEPLOY_DIR/agent"

echo "=== 1. 创建目录 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "mkdir -p $AGENT_DIR"

echo ""
echo "=== 2. 上传 Agent 文件 ==="
sshpass -p "$PASS" scp -o StrictHostKeyChecking=no \
  agent/__init__.py \
  agent/__main__.py \
  agent/agent.py \
  agent/base.py \
  agent/linux.py \
  agent/windows.py \
  $USER@$HOST:$AGENT_DIR/

echo ""
echo "=== 3. 验证文件 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "cd $AGENT_DIR && ls -la"

echo ""
echo "=== 4. 测试模块导入 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "cd $AGENT_DIR && python3 -c \"import agent; print('✅ Agent 模块导入成功')\""

echo ""
echo "=== 5. 测试入口函数 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "cd $AGENT_DIR && python3 -m agent --help"

echo ""
echo "=== 6. 停止旧进程 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "pkill -9 -f 'agent' 2>/dev/null || true"

echo ""
echo "=== 7. 启动 Agent ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "cd $AGENT_DIR && nohup python3 -m agent --host 0.0.0.0 --port 9000 > agent.log 2>&1 &"

echo ""
echo "=== 8. 等待启动 ==="
sleep 3

echo ""
echo "=== 9. 检查进程 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "ps aux | grep -E 'agent.py|python.*agent' | grep -v grep"

echo ""
echo "=== 10. 检查日志 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "cat $AGENT_DIR/agent.log"

echo ""
echo "=== 11. 测试端口 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no $USER@$HOST "curl -s http://localhost:9000/ || echo '端口测试失败'"

echo ""
echo "=== 完成 ==="
