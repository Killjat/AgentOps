#!/usr/bin/env python3
"""
监控 Android Agent 连接稳定性，运行 30 分钟，每 30 秒采样一次
"""
import time
import json
import urllib.request
import urllib.error
from datetime import datetime

SERVER = "http://localhost:8000"
AGENT_ID = "android-51c6656c"
DURATION = 30 * 60  # 30 分钟
INTERVAL = 30       # 每 30 秒采样

stats = {
    "samples": 0,
    "online": 0,
    "offline": 0,
    "reconnects": 0,
    "last_status": None,
    "last_seen_list": [],
}

def get_agent():
    try:
        req = urllib.request.Request(f"{SERVER}/agents/{AGENT_ID}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return None

def fmt(ts):
    return ts[:19].replace("T", " ") if ts else "N/A"

print(f"开始监控 {AGENT_ID}，持续 30 分钟，每 {INTERVAL} 秒采样")
print(f"{'时间':<20} {'状态':<10} {'最后在线':<20} {'CPU':<8} {'重连次数'}")
print("-" * 75)

start = time.time()
while time.time() - start < DURATION:
    agent = get_agent()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats["samples"] += 1

    if agent:
        status = agent.get("status", "unknown")
        last_seen = fmt(agent.get("last_seen"))
        metrics = agent.get("metrics") or {}
        cpu = metrics.get("cpu_usage", -1)
        cpu_str = f"{cpu:.1f}%" if cpu >= 0 else "N/A"

        if stats["last_status"] == "online" and status == "offline":
            stats["reconnects"] += 1
        if status == "online":
            stats["online"] += 1
        else:
            stats["offline"] += 1

        stats["last_status"] = status
        stats["last_seen_list"].append(last_seen)

        print(f"{now:<20} {status:<10} {last_seen:<20} {cpu_str:<8} {stats['reconnects']}")
    else:
        stats["offline"] += 1
        print(f"{now:<20} {'ERROR':<10} {'API 请求失败':<20} {'N/A':<8} {stats['reconnects']}")

    time.sleep(INTERVAL)

# 汇总报告
print("\n" + "=" * 75)
print("监控报告")
print("=" * 75)
print(f"总采样次数:   {stats['samples']}")
print(f"在线次数:     {stats['online']}  ({stats['online']/stats['samples']*100:.1f}%)")
print(f"离线次数:     {stats['offline']}  ({stats['offline']/stats['samples']*100:.1f}%)")
print(f"断线重连次数: {stats['reconnects']}")
uptime = stats['online'] / stats['samples'] * 100
print(f"稳定性评分:   {'优秀' if uptime >= 95 else '良好' if uptime >= 80 else '较差'} ({uptime:.1f}% 在线率)")
