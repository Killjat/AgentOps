#!/bin/bash
# CyberAgentOps 一键重新部署脚本（无需手动输入密码）

set -e

REMOTE_HOST="47.111.28.162"
REMOTE_USER="root"
REMOTE_PASS="kqvfhpsiq@099211"
REMOTE_DIR="/opt/cyberagentops"

# 检查 sshpass
if ! command -v sshpass &>/dev/null; then
    echo "安装 sshpass..."
    brew install sshpass 2>/dev/null || apt-get install -y sshpass 2>/dev/null
fi

SSH="sshpass -p '$REMOTE_PASS' ssh -o StrictHostKeyChecking=no $REMOTE_USER@$REMOTE_HOST"
SCP="sshpass -p '$REMOTE_PASS' scp -o StrictHostKeyChecking=no"

echo "=================================="
echo "CyberAgentOps 重新部署到 $REMOTE_HOST"
echo "=================================="

# 1. 打包
echo "[1/4] 打包项目..."
tar --exclude='.git' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='hosts.yaml' \
    --exclude='users.json' \
    --exclude='agent/dist' \
    --exclude='agent/build' \
    --exclude='agent/android' \
    --exclude='netcheck_android' \
    -czf /tmp/cyberagentops.tar.gz \
    agent/ server/ web/ swarm/ netcheck/ scripts/ requirements.txt .env.example

# 2. 上传
echo "[2/4] 上传文件..."
eval "$SSH 'mkdir -p $REMOTE_DIR'"
eval "$SCP /tmp/cyberagentops.tar.gz $REMOTE_USER@$REMOTE_HOST:/tmp/"
rm /tmp/cyberagentops.tar.gz

# 3. 解压 + 重启服务
echo "[3/4] 更新并重启..."
eval "$SSH" << ENDSSH
set -e
cd $REMOTE_DIR
tar -xzf /tmp/cyberagentops.tar.gz --strip-components=0
rm /tmp/cyberagentops.tar.gz
pip3 install -r requirements.txt -q 2>/dev/null

# 同步 nginx 配置（cyberagentops 主配置）
cp $REMOTE_DIR/deploy/nginx.conf /etc/nginx/sites-available/cyberagentops
ln -sf /etc/nginx/sites-available/cyberagentops /etc/nginx/sites-enabled/cyberagentops
nginx -t && systemctl reload nginx 2>/dev/null || true

# 更新 systemd 服务文件
cat > /etc/systemd/system/cyberagentops.service << EOF
[Unit]
Description=CyberAgentOps Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=$REMOTE_DIR/.env
ExecStart=/usr/bin/python3 $REMOTE_DIR/server/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cyberagentops
systemctl restart cyberagentops
sleep 2
systemctl is-active cyberagentops && echo "✅ 服务运行正常" || echo "❌ 服务启动失败"
ENDSSH

# 4. 完成
echo "[4/4] 完成！"
echo "=================================="
echo "访问地址: https://$REMOTE_HOST"
echo "=================================="
