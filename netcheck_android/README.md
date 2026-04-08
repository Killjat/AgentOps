# CyberAgentOps Android Agent

## 功能
- WebSocket 连接 server，断线自动重连（指数退避）
- 接收并执行 shell 命令
- 上报设备指标（CPU、内存、磁盘、网络）
- 后台常驻（ForegroundService）
- 开机自启

## 编译

1. 用 Android Studio 打开 `agent/android` 目录
2. 等待 Gradle 同步
3. Build → Generate Signed APK 或直接 Run

## 使用

1. 安装 APK
2. 打开 App，输入 Server URL（如 `https://47.111.28.162`）
3. 点击「启动 Agent」
4. 在 CyberAgentOps 控制台的 Agent 管理页面即可看到该设备

## 支持的消息类型

| 类型 | 说明 |
|------|------|
| `exec` | 执行 shell 命令 |
| `metrics` | 采集设备指标 |
| `ping` | 心跳检测 |
