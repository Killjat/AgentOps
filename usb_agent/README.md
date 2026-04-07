# USB Agent 快速部署

将此目录复制到 U 盘，插入自己的电脑后运行对应启动脚本即可自动连接控制服务器。

## 使用步骤

### 1. 编译 agent 二进制（在各平台分别执行）

```bash
# 在项目根目录
./build_agent.sh
```

### 2. 打包到 USB

```bash
./usb_agent/build_usb.sh
```

### 3. 复制到 U 盘

将整个 `usb_agent/` 目录内容复制到 U 盘根目录。

### 4. 在目标机器上运行

| 系统 | 操作 |
|------|------|
| macOS / Linux | 双击或终端运行 `launch.sh` |
| Windows | 双击 `launch.bat` |

Agent 会在后台运行并连接服务器，日志写入 `agent.log`。

## 目录结构

```
usb_agent/
├── launch.sh          # macOS/Linux 启动脚本
├── launch.bat         # Windows 启动脚本
├── build_usb.sh       # 打包脚本
├── bin/
│   ├── cyberagent-mac
│   ├── cyberagent-linux
│   └── cyberagent-windows.exe
└── agent.log          # 运行日志（自动生成）
```
