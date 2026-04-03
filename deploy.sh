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
PYTHON_ABS_PATH=$(which $PYTHON)
echo "  服务将使用的 Python: $PYTHON_ABS_PATH"
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=CyberAgentOps Server
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
systemctl restart $SERVICE_NAME
sleep 2

# 5. 配置 nginx（如果已安装）
echo ""
echo "[4/4] 配置 nginx..."
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

    # 生成 Nginx 配置文件
    cat > $NGINX_CONF_FILE << 'NGINX'
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

    # 启用配置
    eval $ENABLE_CMD
    eval $DISABLE_DEFAULT

    # 测试并重启 Nginx
    if nginx -t 2>&1; then
        systemctl restart nginx
        echo "✅ nginx 已配置，HTTPS 443 端口"
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
