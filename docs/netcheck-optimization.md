# NetCheck 优化路线图

## 优先级排序

| 优先级 | 项目 | 难度 | 价值 |
|--------|------|------|------|
| P0 | SSRF 防护 | 低 | 安全必须 |
| P0 | 并发引擎（Semaphore） | 中 | 性能基础 |
| P1 | L4/L7 分级检测 | 中 | 核心功能 |
| P1 | 状态抖动过滤 | 低 | 用户体验 |
| P2 | WebSocket 替代轮询 | 中 | 性能优化 |
| P2 | 延迟趋势图（Chart.js） | 中 | 数据价值 |
| P3 | 多拨测点维度 | 高 | 差异化卖点 |

---

## 1. SSRF 防护（P0，立即实现）

**风险**：用户输入 `http://127.0.0.1:6379` 可扫描服务器内网。

**实现**：在 `netcheck/router.py` 的入口校验目标地址：

```python
import ipaddress, socket

BLOCKED_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]

def is_safe_target(target: str) -> bool:
    try:
        ip = socket.gethostbyname(target.split("/")[0])
        addr = ipaddress.ip_address(ip)
        return not any(addr in net for net in BLOCKED_RANGES)
    except Exception:
        return False
```

---

## 2. 并发引擎优化（P0）

**现状**：`asyncio.gather` 无限制并发，目标多时可能被封。

**实现**：加 Semaphore 控制并发池：

```python
sem = asyncio.Semaphore(50)  # 最多50并发

async def probe_with_limit(agent_id):
    async with sem:
        return await probe_one(agent_id)

results = await asyncio.gather(*[probe_with_limit(aid) for aid in agent_ids])
```

---

## 3. L4/L7 分级检测（P1）

**现状**：只做 traceroute + curl 延迟，无法判断服务是否真正可用。

**分级方案**：

```
L3 (ICMP)  → ping -c 3 {target}
L4 (TCP)   → nc -zv {target} {port} 或 curl --connect-timeout 3 {target}:{port}
L7 (HTTP)  → curl -s -o /dev/null -w "%{http_code}" https://{target}
L7+ (内容) → curl -s https://{target}/health | grep '"status":"ok"'
```

**在 checker.py 中新增**：

```python
CMDS["l4_check"] = "nc -zv {target} {port} 2>&1 | head -2"
CMDS["l7_check"] = "curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 https://{target}"
CMDS["l7_content"] = "curl -s --max-time 5 https://{target} | grep -c '{keyword}'"
```

---

## 4. 状态抖动过滤（P1）

**现状**：单次失败即标红，网络瞬时波动导致误报。

**实现**：在前端或后端维护状态计数器：

```javascript
// 前端实现
const failCount = ref(0)
const successCount = ref(0)
const FAIL_THRESHOLD = 3   // 连续3次失败才标红
const OK_THRESHOLD = 2     // 连续2次成功才恢复绿

function updateStatus(isSuccess) {
  if (isSuccess) {
    failCount.value = 0
    successCount.value++
    if (successCount.value >= OK_THRESHOLD) realStatus.value = 'ok'
  } else {
    successCount.value = 0
    failCount.value++
    if (failCount.value >= FAIL_THRESHOLD) realStatus.value = 'fail'
  }
}
```

---

## 5. WebSocket 替代轮询（P2）

**现状**：前端 `setInterval` 每3秒轮询，用户多时后端压力大。

**方案**：后端检测完成后通过现有 WebSocket 连接推送结果。

利用已有的 `_ws_connections` 机制，在 `_run_check` 完成后推送：

```python
# 检测完成后推送到前端（通过 agent WebSocket 或新建前端 WS）
# 或者：SSE（Server-Sent Events）更简单，不需要双向通信
```

---

## 6. 延迟趋势图（P2）

**现状**：只看当前值，不知道什么时候开始变慢。

**实现**：
- 将每次检测结果存入 SQLite（已有 `cyberagentops.db`）
- 新增 `netcheck_history` 表：`(id, target, agent_id, latency_ms, timestamp)`
- 前端用 Chart.js 展示 24 小时延迟曲线

```sql
CREATE TABLE IF NOT EXISTS netcheck_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT,
    agent_id TEXT,
    latency_ms REAL,
    loss INTEGER DEFAULT 0,
    timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_nh_target ON netcheck_history(target, timestamp DESC);
```

---

## 7. 多拨测点维度（P3，AgentOps 核心差异化）

**现状**：目标侦察已支持多节点并发 traceroute。

**进阶**：持续监控模式下，同一目标从所有在线 agent 同时探测，生成"可用性热力图"：

```
目标: api.tiktok.com
┌─────────────┬──────┬──────┬──────┐
│ 节点        │ 延迟 │ 丢包 │ 状态 │
├─────────────┼──────┼──────┼──────┤
│ 香港        │ 12ms │  0%  │  ✅  │
│ 阿里云      │  8ms │  0%  │  ✅  │
│ 美国        │160ms │  0%  │  ✅  │
│ 华为手机    │ 25ms │  0%  │  ✅  │
│ 荣耀手机    │ 30ms │  2%  │  ⚠️  │
└─────────────┴──────┴──────┴──────┘
```

这种视图能立即判断是"目标挂了"还是"某条线路有问题"。

---

## 实施顺序建议

1. **本周**：SSRF 防护 + Semaphore（安全 + 稳定性）
2. **下周**：L7 检测 + 抖动过滤（功能完善）
3. **下下周**：延迟趋势图（数据价值）
4. **后续**：WebSocket 推送 + 多拨测点热力图（产品差异化）

---

## 8. 可视化升级方案（视觉设计）

### 8.1 雷达图 / 扇形扩散布局

目标放中心，Agent 节点环绕，连线上流动光点表示延迟：

```
技术实现：Canvas API 或 SVG + requestAnimationFrame
- 中心大圆点 = 目标服务器
- 周围小圆点 = 各 Agent 节点
- 连线光点速度 = 1000/latency_ms（延迟越低越快）
- 断线 = 连线变红 + 断裂动画
库推荐：Anime.js（轻量，无需构建工具）
```

### 8.2 地理分布地图

暗色世界地图，Agent 向目标发射弧线：

```
技术实现：ECharts（单文件引入，无需构建）
- 数据：Agent 的 IP → ipinfo.io 获取经纬度
- 效果：echarts.registerMap + lines3D 或 effectScatter
- 配色：深蓝背景 + 荧光绿弧线 + 警示红断线
```

### 8.3 信号格阵列（大规模节点）

节点多时用 10x10 像素方块热力图：

```
技术实现：纯 CSS Grid + Vue v-for
- 绿色 = 正常（< 100ms）
- 橙色 = 慢（100-300ms）
- 红色 = 断连
- hover 浮窗：IP、城市、实时延迟
参考：GitHub Contribution Graph
```

### 8.4 实时控制台（Terminal Feed）

右侧/底部黑框滚动日志：

```
[10:24:01] 香港节点    → tiktok.com: 200 OK (12ms)
[10:24:02] 阿里云      → tiktok.com: 200 OK (8ms)
[10:24:05] 美国节点    → tiktok.com: TIMEOUT (重试中...)
[10:24:07] 华为手机    → tiktok.com: 200 OK (25ms)

技术实现：Vue ref 数组 + CSS overflow-y:auto + 自动滚底
配色：黑底 + 等宽字体 + 荧光绿文字
```

### 8.5 多线延迟对比图

横轴时间，纵轴延迟，不同颜色代表不同 Agent：

```
技术实现：Chart.js（已引入）
- 多条折线，每条对应一个 Agent
- 判断逻辑：
  - 所有线同时飙升 → 目标服务器问题
  - 只有一条线飙升 → 该 Agent 单点网络问题
```

---

## 技术栈建议

| 需求 | 推荐方案 | 理由 |
|------|---------|------|
| 动效 | Anime.js | 轻量，单文件引入，无需构建 |
| 地图 | ECharts | 单文件引入，中国地图支持好 |
| 配色 | Cyberpunk Dark | 深蓝/深灰背景 + 荧光绿/警示红 |
| 字体 | JetBrains Mono | 等宽，极客感 |
| 布局 | CSS Grid + Flexbox | 无需 Tailwind，保持单文件 |

---

## 实施顺序

1. **P0（本周）**：SSRF 防护 + Semaphore ✅ 已完成
2. **P1（下周）**：Terminal Feed + 多线延迟对比图（Chart.js 已有）
3. **P2**：雷达图布局（Canvas）
4. **P3**：地理分布地图（ECharts）
5. **P4**：信号格阵列（大规模节点场景）
