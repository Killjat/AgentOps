#!/usr/bin/env python3
"""
Linux 运维 Agent - 客户端工具
用于远程下发任务到 Agent 服务器
"""

import requests
import json
import time
import sys
from typing import Optional
import argparse

class AgentClient:
    """Agent 客户端"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
    
    def submit_task(self, task: str, auto_confirm: bool = False, 
                   dry_run: bool = False, timeout: int = 30) -> dict:
        """提交任务"""
        url = f"{self.base_url}/tasks"
        data = {
            "task": task,
            "auto_confirm": auto_confirm,
            "dry_run": dry_run,
            "timeout": timeout
        }
        
        response = requests.post(url, json=data)
        response.raise_for_status()
        return response.json()
    
    def get_task(self, task_id: str) -> dict:
        """获取任务状态"""
        url = f"{self.base_url}/tasks/{task_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    
    def list_tasks(self, status: Optional[str] = None, limit: int = 100) -> list:
        """获取任务列表"""
        url = f"{self.base_url}/tasks"
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def cancel_task(self, task_id: str) -> dict:
        """取消任务"""
        url = f"{self.base_url}/tasks/{task_id}"
        response = requests.delete(url)
        response.raise_for_status()
        return response.json()
    
    def wait_for_task(self, task_id: str, poll_interval: float = 1.0) -> dict:
        """等待任务完成"""
        while True:
            task = self.get_task(task_id)
            status = task['status']
            
            if status in ['success', 'failed', 'cancelled']:
                return task
            
            time.sleep(poll_interval)
    
    def health_check(self) -> dict:
        """健康检查"""
        url = f"{self.base_url}/health"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()


def print_task(task: dict):
    """打印任务信息"""
    print(f"\n{'='*60}")
    print(f"任务 ID: {task['task_id']}")
    print(f"状态: {task['status']}")
    print(f"任务: {task['task']}")
    
    if task.get('command'):
        print(f"命令: {task['command']}")
    
    if task.get('output'):
        print(f"\n输出:\n{task['output']}")
    
    if task.get('error'):
        print(f"\n错误:\n{task['error']}")
    
    print(f"创建时间: {task['created_at']}")
    if task.get('completed_at'):
        print(f"完成时间: {task['completed_at']}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Linux 运维 Agent 客户端")
    parser.add_argument("--server", default="http://localhost:8000",
                       help="Agent 服务器地址")
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # submit 命令
    submit_parser = subparsers.add_parser("submit", help="提交任务")
    submit_parser.add_argument("task", help="任务描述")
    submit_parser.add_argument("--auto", action="store_true", help="自动确认")
    submit_parser.add_argument("--dry-run", action="store_true", help="仅生成命令")
    submit_parser.add_argument("--timeout", type=int, default=30, help="超时时间")
    submit_parser.add_argument("--wait", action="store_true", help="等待任务完成")
    
    # get 命令
    get_parser = subparsers.add_parser("get", help="获取任务状态")
    get_parser.add_argument("task_id", help="任务 ID")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出任务")
    list_parser.add_argument("--status", help="按状态过滤")
    list_parser.add_argument("--limit", type=int, default=20, help="限制数量")
    
    # cancel 命令
    cancel_parser = subparsers.add_parser("cancel", help="取消任务")
    cancel_parser.add_argument("task_id", help="任务 ID")
    
    # health 命令
    health_parser = subparsers.add_parser("health", help="健康检查")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    client = AgentClient(args.server)
    
    try:
        if args.command == "submit":
            print(f"📤 提交任务: {args.task}")
            task = client.submit_task(
                args.task,
                auto_confirm=args.auto,
                dry_run=args.dry_run,
                timeout=args.timeout
            )
            print(f"✅ 任务已提交: {task['task_id']}")
            
            if args.wait:
                print("⏳ 等待任务完成...")
                task = client.wait_for_task(task['task_id'])
                print_task(task)
            else:
                print(f"💡 查看状态: python {sys.argv[0]} get {task['task_id']}")
        
        elif args.command == "get":
            task = client.get_task(args.task_id)
            print_task(task)
        
        elif args.command == "list":
            tasks = client.list_tasks(status=args.status, limit=args.limit)
            print(f"\n📋 任务列表 (共 {len(tasks)} 个):\n")
            
            for task in tasks:
                status_emoji = {
                    'pending': '⏳',
                    'running': '⚙️',
                    'success': '✅',
                    'failed': '❌',
                    'cancelled': '🚫'
                }.get(task['status'], '❓')
                
                print(f"{status_emoji} [{task['status']}] {task['task_id'][:8]}... - {task['task']}")
        
        elif args.command == "cancel":
            result = client.cancel_task(args.task_id)
            print(f"✅ {result['message']}")
        
        elif args.command == "health":
            health = client.health_check()
            print(f"\n🏥 健康检查:")
            print(f"  服务状态: {health['status']}")
            print(f"  模型状态: {health['model_status']}")
            print(f"  模型类型: {health['model_type']}")
            print(f"  模型名称: {health['model_name']}")
            print(f"  总任务数: {health['total_tasks']}")
            print(f"  WebSocket 连接: {health['websocket_connections']}\n")
    
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到服务器: {args.server}")
        print("请确保 Agent 服务器正在运行")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
