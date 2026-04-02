#!/bin/bash
# CyberAgentOps 服务端部署脚本
# 放在 GitHub 仓库根目录，clone 后自动执行
# 用法：bash deploy.sh

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="cyberagentops"
PYTHON="python3"

echo "=================================="
echo "CyberAgentOps 部署脚本"
echo "目录: $APP_DIR"
echo "=================================="

# 1. 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "❌ 未找到 python3，请先安装"
    exit 1
fi
echo "✅ Python: $($PYTHON --version)"

# 2. 安装依赖
echo ""
echo "[1/4] 安装 Python 依赖..."
$PYTHON -m pip install -r "$APP_DIR/requirements.txt" -q \
    --break-system-packages 2>/dev/null \
    || $PYTHON -m pip install -r "$APP_DIR/requirements.txt" -q
echo "✅ 依赖安装完成"

# 3. 检查 .env
echo ""
echo "[2/4] 检查配置文件..."
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        echo "⚠️  已从 .env.example 创建 .env，请编辑填入真实配置："
        echo "   vim $APP_DIR/.env"
    else
        echo "⚠️  未找到 .env 文件，请手动创建"
    fi
else
    echo "✅ .env 已存在"
fi

# 4. 注册 systemd 服务
echo ""
echo "[3/4] 配置 systemd 服务..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=CyberAgentOps Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYTHON $APP_DIR/server/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME
sleep 2

# 5. 配置 nginx（如果已安装）
echo ""
echo "[4/4] 配置 nginx..."
if command -v nginx &>/dev/null; then
    # 生成自签证书（如果不存在）
    mkdir -p /etc/nginx/ssl
    if [ ! -f /etc/nginx/ssl/${SERVICE_NAME}.crt ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout /etc/nginx/ssl/${SERVICE_NAME}.key \
            -out /etc/nginx/ssl/${SERVICE_NAME}.crt \
            -subj "/CN=$(hostname -I | awk '{print $1}')" 2>/dev/null
        echo "✅ 自签证书已生成"
    fi

    cat > /etc/nginx/sites-available/${SERVICE_NAME} << 'NGINX'
server {
    listen 80;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    ssl_certificate     /etc/nginx/ssl/cyberagentops.crt;
    ssl_certificate_key /etc/nginx/ssl/cyberagentops.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
NGINX

    ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null
    nginx -t && systemctl restart nginx
    echo "✅ nginx 已配置，HTTPS 443 端口"
else
    echo "⚠️  未安装 nginx，服务运行在 http://localhost:8000"
fi

# 完成
echo ""
echo "=================================="
STATUS=$(systemctl is-active $SERVICE_NAME 2>/dev/null || echo "unknown")
if [ "$STATUS" = "active" ]; then
    echo "✅ 部署成功！服务运行中"
    IP=$(hostname -I | awk '{print $1}')
    echo "   访问地址: https://$IP"
else
    echo "❌ 服务启动失败，查看日志："
    echo "   journalctl -u $SERVICE_NAME -n 30"
fi
echo "=================================="
