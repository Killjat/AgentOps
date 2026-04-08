#!/bin/bash
# CyberAgentOps 服务端部署脚本
# 放在 GitHub 仓库根目录，clone 后自动执行
# 用法：bash deploy.sh

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="cyberagentops"
PYTHON="python3"
SSL_CERT_DIR="/etc/ssl/certs"
SSL_KEY_DIR="/etc/ssl/private"

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

# 1.5 停止现有服务并清理端口占用
echo ""
echo "[停止现有服务并清理端口]..."
if systemctl is-active --quiet $SERVICE_NAME 2>/dev/null; then
    echo "  正在停止服务 $SERVICE_NAME..."
    systemctl stop $SERVICE_NAME
    sleep 3
fi

# 禁用服务自启和自动重启，防止后台拉起
echo "  禁用服务自启和自动重启..."
systemctl disable $SERVICE_NAME 2>/dev/null || true
sleep 2

# 杀死残留的进程
echo "  清理残留进程..."
pkill -f "python.*server/main.py" 2>/dev/null || true
pkill -f "uvicorn.*main:app" 2>/dev/null || true
sleep 2

# 检查并释放端口 8000
echo "  检查端口 8000 占用情况..."
if ss -tlnp 2>/dev/null | grep -q :8000; then
    echo "  ⚠️  端口 8000 仍被占用，尝试释放..."
    fuser -k 8000/tcp 2>/dev/null || true
    sleep 3
fi

# 验证端口已释放
if ss -tlnp 2>/dev/null | grep -q :8000; then
    echo "  ❌ 端口 8000 仍被占用，显示占用进程："
    lsof -i :8000 2>/dev/null || ss -tlnp 2>/dev/null | grep :8000
    echo "  请手动终止占用进程"
    exit 1
else
    echo "  ✅ 端口 8000 已释放"
fi

# 2. 安装依赖
echo ""
echo "[2/5] 安装 Python 依赖..."
# 确保 pip 可用
if ! command -v pip3 &>/dev/null && ! $PYTHON -m pip --version &>/dev/null 2>&1; then
    echo "安装 pip..."
    dnf install -y python3-pip 2>/dev/null \
        || apt-get install -y python3-pip 2>/dev/null \
        || curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON
fi

# 安装依赖（支持多种方式）
echo "  安装 requirements.txt 依赖..."
echo "  Python 路径: $(which $PYTHON)"
echo "  Pip 路径: $($PYTHON -m pip --version | head -1)"
pip3 install -r "$APP_DIR/requirements.txt" -q 2>/dev/null \
    || $PYTHON -m pip install -r "$APP_DIR/requirements.txt" -q --break-system-packages 2>/dev/null \
    || $PYTHON -m pip install -r "$APP_DIR/requirements.txt" -q --user 2>/dev/null \
    || $PYTHON -m pip install -r "$APP_DIR/requirements.txt" -q

# 关键修复：确保 aiohttp 被安装（即使 requirements.txt 中没有）
echo "  安装 aiohttp..."
$PYTHON -m pip install aiohttp>=3.9.0 -q 2>/dev/null \
    || $PYTHON -m pip install aiohttp>=3.9.0 --break-system-packages -q 2>/dev/null \
    || $PYTHON -m pip install aiohttp>=3.9.0 --user -q 2>/dev/null \
    || pip3 install aiohttp>=3.9.0 -q

# 验证 aiohttp 是否成功安装
if ! $PYTHON -c "import aiohttp" 2>/dev/null; then
    echo "❌ aiohttp 安装失败，尝试强制安装..."
    echo "  Python 版本: $($PYTHON --version)"
    echo "  Python 路径: $(which $PYTHON)"
    $PYTHON -m pip install aiohttp --force-reinstall
    if ! $PYTHON -c "import aiohttp" 2>/dev/null; then
        echo "❌ aiohttp 安装失败，部署可能无法正常工作"
        echo "  请手动检查 Python 环境是否一致"
        exit 1
    fi
fi
echo "✅ aiohttp 安装成功 (版本: $($PYTHON -c 'import aiohttp; print(aiohttp.__version__)'))"
echo "✅ 依赖安装完成"

# 3. 检查 .env
echo ""
echo "[3/6] 检查配置文件..."
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

# 3.5 生成 SSL 证书（如果不存在）
echo ""
echo "[4/6] 配置 SSL 证书..."
SSL_CERT_FILE="$SSL_CERT_DIR/server.crt"
SSL_KEY_FILE="$SSL_KEY_DIR/server.key"

if [ ! -f "$SSL_CERT_FILE" ] || [ ! -f "$SSL_KEY_FILE" ]; then
    echo "  生成自签名 SSL 证书..."
    mkdir -p "$SSL_CERT_DIR" "$SSL_KEY_DIR"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$SSL_KEY_FILE" \
        -out "$SSL_CERT_FILE" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=AgentOps/OU=Dev/CN=localhost" \
        2>/dev/null
    chmod 600 "$SSL_KEY_FILE"
    chmod 644 "$SSL_CERT_FILE"
    echo "  ✅ SSL 证书生成成功"
else
    echo "  ✅ SSL 证书已存在"
fi

# 4. 注册 systemd 服务
echo ""
echo "[5/6] 配置 systemd 服务..."
PYTHON_ABS_PATH=$(which $PYTHON)
echo "  服务将使用的 Python: $PYTHON_ABS_PATH"
echo "  Python 版本: $($PYTHON_ABS_PATH --version)"
echo "  验证 aiohttp 是否可用: $($PYTHON_ABS_PATH -c 'import aiohttp; print(\"aiohttp version:\", aiohttp.__version__)' 2>/dev/null || echo 'aiohttp NOT FOUND')"

cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=${SERVICE_NAME}
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYTHON_ABS_PATH $APP_DIR/server/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME

echo ""
echo "  启动服务前最终检查..."
echo "  Python: $PYTHON_ABS_PATH"
echo "  aiohttp: $($PYTHON_ABS_PATH -c 'import aiohttp; print(aiohttp.__version__)' 2>/dev/null || echo '未安装')"

systemctl restart $SERVICE_NAME
sleep 2

# 检查服务状态
if systemctl is-active --quiet $SERVICE_NAME; then
    echo "  ✅ 服务启动成功"
else
    echo "  ❌ 服务启动失败，查看日志:"
    journalctl -u $SERVICE_NAME -n 20 --no-pager
fi

# 5. 配置 nginx（如果已安装）
echo ""
echo "[6/6] 配置 nginx..."
if command -v nginx &>/dev/null; then
    # 检测系统类型并选择正确的配置路径
    if [ -d "/etc/nginx/sites-available" ]; then
        # Debian/Ubuntu 风格
        NGINX_CONF_DIR="/etc/nginx/sites-available"
        NGINX_ENABLED="/etc/nginx/sites-enabled"
        NGINX_CONF_FILE="$NGINX_CONF_DIR/${SERVICE_NAME}"
        ENABLE_CMD="ln -sf $NGINX_CONF_FILE $NGINX_ENABLED/${SERVICE_NAME}"
        DISABLE_DEFAULT="rm -f /etc/nginx/sites-enabled/default 2>/dev/null"
        echo "检测到 Debian/Ubuntu 风格 Nginx 配置"
    elif [ -d "/etc/nginx/conf.d" ]; then
        # CentOS/RHEL 风格
        NGINX_CONF_DIR="/etc/nginx/conf.d"
        NGINX_CONF_FILE="$NGINX_CONF_DIR/${SERVICE_NAME}.conf"
        ENABLE_CMD="true"  # conf.d 目录下的配置自动生效
        DISABLE_DEFAULT="true"
        echo "检测到 CentOS/RHEL 风格 Nginx 配置"
    else
        # 未知系统，尝试使用 conf.d
        NGINX_CONF_DIR="/etc/nginx/conf.d"
        NGINX_CONF_FILE="$NGINX_CONF_DIR/${SERVICE_NAME}.conf"
        ENABLE_CMD="true"
        DISABLE_DEFAULT="true"
        echo "⚠️  未知 Nginx 配置风格，使用 conf.d 目录"
    fi

    # 生成自签证书（如果不存在）
    mkdir -p /etc/nginx/ssl
    if [ ! -f /etc/nginx/ssl/${SERVICE_NAME}.crt ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout /etc/nginx/ssl/${SERVICE_NAME}.key \
            -out /etc/nginx/ssl/${SERVICE_NAME}.crt \
            -subj "/CN=$(hostname -I | awk '{print $1}')" 2>/dev/null
        echo "✅ 自签证书已生成"
    fi

    # 生成 Nginx 配置文件（从仓库模板复制，保证 WebSocket 支持）
    if [ -f "$APP_DIR/deploy/nginx.conf" ]; then
        cp "$APP_DIR/deploy/nginx.conf" $NGINX_CONF_FILE
        echo "  ✅ 使用仓库 nginx 配置"
    else
        cat > $NGINX_CONF_FILE << NGINX
server {
    listen 80;
    return 301 https://\$host:8443\$request_uri;
}
server {
    listen 8443 ssl;
    ssl_certificate     /etc/nginx/ssl/${SERVICE_NAME}.crt;
    ssl_certificate_key /etc/nginx/ssl/${SERVICE_NAME}.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX
    fi

    # 启用配置
    eval $ENABLE_CMD
    eval $DISABLE_DEFAULT

    # 测试并重启 Nginx
    if nginx -t 2>&1; then
        systemctl restart nginx
        echo "✅ nginx 已配置，HTTPS 8443 端口"
    else
        echo "⚠️  nginx 配置测试失败，请检查配置文件: $NGINX_CONF_FILE"
    fi
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
