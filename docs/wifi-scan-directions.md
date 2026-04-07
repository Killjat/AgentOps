# WiFi 扫描能力 — 技术方向与应用场景

## 技术边界

### 可以做的（合法，Android 系统 API 公开支持）
- 扫描周边 WiFi 热点：SSID、BSSID、信号强度（RSSI）、频段（2.4G/5G）、加密类型（WPA2/WPA3/开放）
- 统计周边热点数量和分布
- 结合 GPS 坐标做地理标注
- 监测信号强度变化

### 做不到的（系统封锁）
- 读取已连接 WiFi 的密码（Android 6+ 封死，需 root）
- 获取其他设备的 MAC 地址（Android 10+ 随机化）
- 主动连接未授权网络

### 权限要求
- `ACCESS_FINE_LOCATION`（Android 要求定位权限才能扫 WiFi）
- `CHANGE_WIFI_STATE`（触发扫描）
- Android 9+ 节流限制：前台每 2 分钟最多 4 次扫描

---

## 技术实现

```kotlin
// WiFiCapability.kt
class WifiCapability : CapabilityHandler {
    override val description = "扫描周边 WiFi 热点，返回 SSID、信号强度、加密类型"

    override fun execute(context: Context, params: JSONObject): ExecResult {
        val wifiManager = context.applicationContext
            .getSystemService(Context.WIFI_SERVICE) as WifiManager

        wifiManager.startScan()
        val results = wifiManager.scanResults

        val list = results.map { ap ->
            JSONObject().apply {
                put("ssid", ap.SSID)
                put("bssid", ap.BSSID)
                put("rssi", ap.level)          // 信号强度 dBm
                put("frequency", ap.frequency) // MHz，2.4G≈2412-2484，5G≈5180-5825
                put("security", getSecurityType(ap.capabilities))
            }
        }

        return ExecResult(true, JSONArray(list).toString(), "")
    }

    private fun getSecurityType(capabilities: String): String = when {
        capabilities.contains("WPA3") -> "WPA3"
        capabilities.contains("WPA2") -> "WPA2"
        capabilities.contains("WPA")  -> "WPA"
        capabilities.contains("WEP")  -> "WEP"
        else -> "OPEN"
    }
}
```

返回示例：
```json
[
  { "ssid": "CafeWiFi", "bssid": "aa:bb:cc:dd:ee:ff", "rssi": -45, "frequency": 2437, "security": "WPA2" },
  { "ssid": "HomeNet_5G", "bssid": "11:22:33:44:55:66", "rssi": -62, "frequency": 5180, "security": "WPA3" },
  { "ssid": "", "bssid": "77:88:99:aa:bb:cc", "rssi": -78, "frequency": 2462, "security": "OPEN" }
]
```

---

## 应用场景

### 1. 室内定位（WiFi 指纹定位）

**原理：** 同一位置扫描到的 WiFi 热点组合（BSSID + RSSI）具有唯一性，称为"WiFi 指纹"。
预先采集各位置的指纹建库，之后实时扫描与库匹配即可定位，精度 1-3 米。

**适用场景：** 商场、展馆、医院、仓库等 GPS 信号弱的室内环境。

**Swarm 用法：** 多台手机分布在不同位置同时采集，快速建立指纹库。

---

### 2. 商业场所客流分析

**原理：** 统计周边 WiFi 探针（路由器）数量和信号变化，间接推断区域内设备密度。

**适用场景：**
- 商场统计各楼层客流密度
- 展会统计展位人气
- 连锁门店选址评估（周边 WiFi 密度反映商业活跃度）

**数据价值：** 零售商、地产商、广告商愿意为精准客流数据付费。

---

### 3. 网络质量众测

**原理：** 大量手机在不同地点持续扫描，汇总 WiFi 覆盖密度、信号强度分布。

**适用场景：**
- 运营商评估热点覆盖质量
- 企业评估办公区域网络盲区
- 城市 WiFi 覆盖地图（政府/运营商采购）

---

### 4. 企业网络安全巡检

**原理：** 扫描企业内部出现的未授权热点（Rogue AP），发现潜在的钓鱼 WiFi 或员工私设热点。

**适用场景：**
- 企业安全团队定期巡检
- 金融、政府等高安全要求场所的持续监控

**Swarm 用法：** 多台手机分布在办公楼各层，统一扫描上报，服务器对比白名单自动告警。

---

### 5. 多节点 WiFi 热力图

**原理：** 多台手机同时扫描同一区域，每台上报坐标 + WiFi 列表，服务器聚合生成热力图。

**Swarm 任务示例：**
```
目标：绘制商场一楼 WiFi 覆盖热力图
→ android-A（入口）: get_wifi_list + get_location
→ android-B（中庭）: get_wifi_list + get_location
→ android-C（出口）: get_wifi_list + get_location
→ 服务器汇总，生成覆盖地图
```

---

## 与现有 Swarm 架构的结合

在 `planner.py` 的 prompt 中，当 agent 具备 `get_wifi_list` 能力时，LLM 可以规划：

```
- 网络侦察任务：多节点并行扫描，汇总周边 WiFi 分布
- 位置感知任务：结合 GPS + WiFi 指纹，精确定位各 agent 位置
- 安全审计任务：扫描指定区域，发现开放或弱加密热点
```

---

## 开发优先级

| 阶段 | 内容 |
|------|------|
| P1 | 基础扫描：SSID + RSSI + 加密类型，集成到 CapabilityRegistry |
| P1 | 与 get_location 联动，每次扫描自动附带 GPS 坐标 |
| P2 | 服务器端聚合 API，支持多节点数据合并 |
| P2 | Web 界面热力图可视化 |
| P3 | WiFi 指纹采集与匹配（室内定位） |
