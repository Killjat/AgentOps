#!/usr/bin/env python3
"""
Linux 运维 Agent - 实时监控客户端
通过 WebSocket 实时接收任务更新
"""

import asyncio
import websockets
import json
import sys
from datetime import datetime

async def monitor(server_url: str = "ws://localhost:8000/ws"):
    """监控任务更新"""
    print("=" * 60)
    print("📡 Linux 运维 Agent 实时监控")
    print(f"🔗 连接到: {server_url}")
    print("=" * 60)
    
    try:
        async with websockets.connect(server_url) as websocket:
            print("✅ 已连接到服务器\n")
            
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                
                msg_type = data.get('type')
                
                if msg_type == 'connected':
                    print(f"💬 {data['message']}")
                    print(f"📊 总任务数: {data['total_tasks']}\n")
                
                elif msg_type == 'task_update':
                    task = data['data']
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    
                    status_emoji = {
                        'pending': '⏳',
                        'running': '⚙️',
                        'success': '✅',
                        'failed': '❌',
                        'cancelled': '🚫'
                    }.get(task['status'], '❓')
                    
                    print(f"[{timestamp}] {status_emoji} 任务更新:")
                    print(f"  ID: {task['task_id'][:8]}...")
                    print(f"  状态: {task['status']}")
                    print(f"  任务: {task['task']}")
                    
                    if task.get('command'):
                        print(f"  命令: {task['command']}")
                    
                    if task.get('output'):
                        output = task['output'][:100]
                        print(f"  输出: {output}...")
                    
                    if task.get('error'):
                        print(f"  错误: {task['error']}")
                    
                    print()
    
    except websockets.exceptions.ConnectionClosed:
        print("\n❌ 连接已断开")
    except KeyboardInterrupt:
        print("\n\n👋 监控已停止")
    except Exception as e:
        print(f"\n❌ 错误: {e}")

if __name__ == "__main__":
    server = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8000/ws"
    asyncio.run(monitor(server))
