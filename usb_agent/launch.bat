@echo off
:: CyberAgentOps USB Launcher - Windows
:: 插入 U 盘后双击运行，自动连接到控制服务器

set SERVER_URL=https://april-outermost-undefeatedly.ngrok-free.dev
set SCRIPT_DIR=%~dp0

echo CyberAgentOps Agent 启动中...
echo 服务器: %SERVER_URL%

set BIN=%SCRIPT_DIR%bin\cyberagent-windows.exe

if not exist "%BIN%" (
    echo 找不到 agent 二进制: %BIN%
    echo 请先运行 build_usb.sh 打包
    pause
    exit /b 1
)

:: 后台静默运行
start /b "" "%BIN%" --server "%SERVER_URL%" >> "%SCRIPT_DIR%agent.log" 2>&1

echo Agent 已在后台启动
echo 日志: %SCRIPT_DIR%agent.log
timeout /t 2 >nul
