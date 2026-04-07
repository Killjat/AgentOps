# Android 原生能力扩展设计

## 背景

当前 Android Agent 只能执行系统 shell 命令（ping、curl 等）。
APK 作为原生应用，天然具备访问摄像头、GPS、传感器、联系人等系统能力，
这些能力无法通过 shell 命令暴露，需要一套独立的扩展机制。

---

## 目标

1. APK 内部可以注册任意原生能力（Capability）
2. 服务器通过 discover 自动发现这些能力
3. Planner 在规划任务时能感知并调用原生能力
4. 执行路径与 shell exec 并行，互不干扰

---

## 架构设计

### 1. 能力声明（Capability Manifest）

APK 在响应 `discover` 消息时，除返回 shell tools，还返回 `capabilities` 数组：

```json
{
  "tools": [...],
  "capabilities": [
    {
      "name": "take_photo",
      "description": "调用摄像头拍一张照片，返回 base64 编码图片",
      "type": "native",
      "params": {
        "quality": "high | medium | low，默认 medium"
      },
      "returns": "base64 图片字符串"
    },
    {
      "name": "get_location",
      "description": "获取当前 GPS 坐标（纬度、经度、精度）",
      "type": "native",
      "params": {},
      "returns": "{ lat, lng, accuracy }"
    },
    {
      "name": "get_battery",
      "description": "获取电池电量和充电状态",
      "type": "native",
      "params": {},
      "returns": "{ level, charging }"
    },
    {
      "name": "send_notification",
      "description": "在设备上弹出一条通知",
      "type": "native",
      "params": {
        "title": "通知标题",
        "body": "通知内容"
      },
      "returns": "{ success }"
    }
  ]
}
```

### 2. 消息类型扩展：`action`

在现有 `exec`（shell）基础上，新增 `action` 消息类型用于调用原生能力：

**服务器 → APK：**
```json
{
  "type": "action",
  "task_id": "abc123",
  "name": "take_photo",
  "params": { "quality": "high" }
}
```

**APK → 服务器：**
```json
{
  "type": "result",
  "task_id": "abc123",
  "success": true,
  "output": "<base64 图片数据>",
  "error": ""
}
```

### 3. APK 内部实现

在 `AgentWebSocket.kt` 的 `handleServerMessage` 中新增 `action` 分支：

```kotlin
"action" -> {
    val actionName = json.optString("name")
    val params = json.optJSONObject("params") ?: JSONObject()
    scope.launch(Dispatchers.Default) {
        val result = CapabilityRegistry.invoke(context, actionName, params)
        ws.send(JSONObject().apply {
            put("type", "result")
            put("task_id", taskId)
            put("success", result.success)
            put("output", result.output)
            put("error", result.error)
            put("done", true)
        }.toString())
    }
}
```

新建 `CapabilityRegistry.kt`，统一注册和分发：

```kotlin
object CapabilityRegistry {

    private val handlers = mutableMapOf<String, CapabilityHandler>()

    init {
        register("take_photo", PhotoCapability())
        register("get_location", LocationCapability())
        register("get_battery", BatteryCapability())
        register("send_notification", NotificationCapability())
    }

    fun register(name: String, handler: CapabilityHandler) {
        handlers[name] = handler
    }

    fun invoke(context: Context, name: String, params: JSONObject): ExecResult {
        val handler = handlers[name]
            ?: return ExecResult(false, "", "未知能力: $name")
        return handler.execute(context, params)
    }

    fun listCapabilities(): List<Map<String, String>> {
        return handlers.map { (name, handler) ->
            mapOf("name" to name, "description" to handler.description, "type" to "native")
        }
    }
}

interface CapabilityHandler {
    val description: String
    fun execute(context: Context, params: JSONObject): ExecResult
}
```

### 4. 服务器端存储

`deploy.py` 的 discover 处理中，将 capabilities 也持久化：

```python
tools = agent_data.get("tools", [])
capabilities = agent_data.get("capabilities", [])

if info.metrics:
    if tools:
        info.metrics["tools"] = tools
    if capabilities:
        info.metrics["capabilities"] = capabilities
    _save_agents()
```

### 5. Planner 感知原生能力

`coordinator.py` 的 `_get_agents_info` 中，把 capabilities 也传给 planner：

```python
result.append({
    "agent_id": aid,
    "os_type": agent.os_type,
    "hostname": ...,
    "status": agent.status,
    "capabilities": (agent.metrics or {}).get("capabilities", []),
})
```

`planner.py` 的 prompt 中注入能力描述：

```
可用 Agent 列表：
- android-51c6656c: android | NAM-AL00 | online
  原生能力: take_photo（拍照）, get_location（GPS定位）, get_battery（电量）
```

Planner 生成的 subtask 中，`instruction` 可以是：
- shell 命令：`ping -c 5 www.baidu.com`
- 原生能力调用：`action://get_location`、`action://take_photo?quality=high`

Executor 根据 instruction 前缀判断走 exec 还是 action 路径。

---

## 开发优先级

| 优先级 | 能力 | 说明 |
|--------|------|------|
| P0 | get_location | GPS 定位，多节点位置感知 |
| P0 | get_battery | 电量监控，避免任务中途断电 |
| P1 | take_photo | 拍照取证 |
| P1 | send_notification | 远程推送通知到设备 |
| P2 | read_contacts | 联系人读取（需权限） |
| P2 | record_audio | 录音（需权限） |
| P2 | get_wifi_list | 扫描周边 WiFi |

---

## 权限处理

原生能力涉及 Android 危险权限（摄像头、位置、麦克风等），需要：

1. `AndroidManifest.xml` 声明权限
2. 运行时动态申请（`ActivityCompat.requestPermissions`）
3. 能力执行前检查权限，未授权时返回明确错误而非崩溃

---

## 与现有架构的兼容性

- 不改变现有 `exec` 消息路径，完全向后兼容
- `discover` 响应新增 `capabilities` 字段，旧版服务器忽略即可
- Planner prompt 中能力描述为可选，没有 capabilities 时退化为纯 shell 模式
