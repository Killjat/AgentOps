#!/bin/bash
# CyberAgentOps 一键部署脚本
# 目标：47.111.28.162，HTTPS 443

set -e

REMOTE_HOST="47.111.28.162"
REMOTE_USER="root"
REMOTE_DIR="/opt/cyberagentops"
DOMAIN=""   # 有域名填域名，没有留空用 IP 自签证书

echo "=================================="
echo "CyberAgentOps 部署到 $REMOTE_HOST"
echo "=================================="

# 1. 打包项目文件（排除不需要的）
echo "[1/5] 打包项目..."
tar --exclude='.git' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='hosts.yaml' \
    --exclude='users.json' \
    -czf /tmp/cyberagentops.tar.gz \
    agent/ server/ web/ requirements.txt .env.example

# 2. 上传
echo "[2/5] 上传文件..."
ssh $REMOTE_USER@$REMOTE_HOST "mkdir -p $REMOTE_DIR"
scp /tmp/cyberagentops.tar.gz $REMOTE_USER@$REMOTE_HOST:/tmp/
rm /tmp/cyberagentops.tar.gz

# 3. 远端部署
echo "[3/5] 远端安装..."
ssh $REMOTE_USER@$REMOTE_HOST bash << ENDSSH
set -e
cd $REMOTE_DIR
tar -xzf /tmp/cyberagentops.tar.gz --strip-components=0
rm /tmp/cyberagentops.tar.gz

# 安装 Python 依赖
pip3 install -r requirements.txt -q

# 创建 .env（如果不存在）
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  请编辑 $REMOTE_DIR/.env 填入 API Key 和密码"
fi

# 创建 systemd 服务
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
echo "✅ 服务已启动"
ENDSSH

# 4. 配置 nginx + SSL
echo "[4/5] 配置 nginx + SSL..."
ssh $REMOTE_USER@$REMOTE_HOST bash << ENDSSH
# 安装 nginx
apt-get install -y nginx 2>/dev/null || yum install -y nginx 2>/dev/null

# 生成自签证书（没有域名时使用）
mkdir -p /etc/nginx/ssl
if [ ! -f /etc/nginx/ssl/cyberagentops.crt ]; then
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/cyberagentops.key \
        -out /etc/nginx/ssl/cyberagentops.crt \
        -subj "/C=CN/ST=Tokyo/L=Tokyo/O=CyberAgentOps/CN=$REMOTE_HOST"
    echo "✅ 自签证书已生成（有效期10年）"
fi

# nginx 配置
cat > /etc/nginx/sites-available/cyberagentops << 'EOF'
server {
    listen 80;
    server_name _;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/ssl/cyberagentops.crt;
    ssl_certificate_key /etc/nginx/ssl/cyberagentops.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
EOF

# 启用站点
ln -sf /etc/nginx/sites-available/cyberagentops /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default 2>/dev/null

nginx -t && systemctl restart nginx
echo "✅ nginx 已配置"

# 开放防火墙
ufw allow 443/tcp 2>/dev/null || true
ufw allow 80/tcp 2>/dev/null || true
iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
ENDSSH

# 5. 完成
echo ""
echo "[5/5] 部署完成！"
echo "=================================="
echo "访问地址: https://$REMOTE_HOST"
echo ""
echo "⚠️  首次部署需要配置 .env："
echo "   ssh $REMOTE_USER@$REMOTE_HOST"
echo "   vim $REMOTE_DIR/.env"
echo ""
echo "填入以下内容："
echo "   DEEPSEEK_API_KEY=your-key"
echo "   ADMIN_USERNAME=admin"
echo "   ADMIN_PASSWORD=your-password"
echo "   SERVER_URL=https://$REMOTE_HOST"
echo ""
echo "然后重启服务："
echo "   systemctl restart cyberagentops"
echo "=================================="
