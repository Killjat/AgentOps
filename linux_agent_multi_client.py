#!/usr/bin/env python3
"""
Linux 运维 Multi-Agent 客户端
管理多个 Agent 和任务分发
"""

import requests
import json
import sys
import argparse
from typing import Optional, List
from tabulate import tabulate

class MultiAgentClient:
    """Multi-Agent 客户端"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
    
    # Agent 管理
    def create_agent(self, name: str, role: str, 
                    llm_provider: str = "deepseek",
                    description: str = None,
                    model_name: str = None, 
                    api_key: str = None,
                    tags: List[str] = None) -> dict:
        """创建 Agent"""
        url = f"{self.base_url}/agents"
        params = {
            "name": name,
            "role": role,
            "llm_provider": llm_provider
        }
        if description:
            params["description"] = description
        if model_name:
            params["model_name"] = model_name
        if api_key:
            params["api_key"] = api_key
        if tags:
            params["tags"] = tags
        
        response = requests.post(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def list_agents(self, role: Optional[str] = None, 
                   status: Optional[str] = None) -> List[dict]:
        """获取 Agent 列表"""
        url = f"{self.base_url}/agents"
        params = {}
        if role:
            params["role"] = role
        if status:
            params["status"] = status
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_agent(self, agent_id: str) -> dict:
        """获取 Agent 详情"""
        url = f"{self.base_url}/agents/{agent_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    
    def delete_agent(self, agent_id: str) -> dict:
        """删除 Agent"""
        url = f"{self.base_url}/agents/{agent_id}"
        response = requests.delete(url)
        response.raise_for_status()
        return response.json()
    
    # 任务管理
    def submit_task(self, task: str, agent_id: Optional[str] = None,
                   role: Optional[str] = None, auto_confirm: bool = False,
                   dry_run: bool = False, timeout: int = 30, 
                   priority: int = 0) -> dict:
        """提交任务"""
        url = f"{self.base_url}/tasks"
        data = {
            "task": task,
            "agent_id": agent_id,
            "role": role,
            "auto_confirm": auto_confirm,
            "dry_run": dry_run,
            "timeout": timeout,
            "priority": priority
        }
        
        response = requests.post(url, json=data)
        response.raise_for_status()
        return response.json()
    
    def list_tasks(self, agent_id: Optional[str] = None,
                  status: Optional[str] = None, limit: int = 100) -> List[dict]:
        """获取任务列表"""
        url = f"{self.base_url}/tasks"
        params = {"limit": limit}
        if agent_id:
            params["agent_id"] = agent_id
        if status:
            params["status"] = status
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_task(self, task_id: str) -> dict:
        """获取任务详情"""
        url = f"{self.base_url}/tasks/{task_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()


def print_agents_table(agents: List[dict]):
    """打印 Agent 表格"""
    from tabulate import tabulate as tabulate_func
    
    if not agents:
        print("没有 Agent")
        return
    
    table_data = []
    for agent in agents:
        status_emoji = {
            'online': '🟢',
            'offline': '🔴',
            'busy': '🟡',
            'error': '⚠️'
        }.get(agent['status'], '❓')
        
        role_emoji = {
            'monitor': '📊',
            'security': '🔒',
            'network': '🌐',
            'database': '💾',
            'devops': '⚙️',
            'general': '🔧'
        }.get(agent['role'], '❓')
        
        table_data.append([
            agent['agent_id'],
            f"{role_emoji} {agent['name']}",
            agent['role'],
            f"{status_emoji} {agent['status']}",
            f"{agent['current_tasks']}/{agent['max_concurrent_tasks']}",
            agent['total_tasks_completed']
        ])
    
    headers = ['ID', '名称', '角色', '状态', '当前/最大任务', '已完成']
    print("\n" + tabulate_func(table_data, headers=headers, tablefmt='grid'))


def print_tasks_table(tasks: List[dict]):
    """打印任务表格"""
    from tabulate import tabulate as tabulate_func
    
    if not tasks:
        print("没有任务")
        return
    
    table_data = []
    for task in tasks:
        status_emoji = {
            'pending': '⏳',
            'running': '⚙️',
            'success': '✅',
            'failed': '❌',
            'cancelled': '🚫'
        }.get(task['status'], '❓')
        
        task_desc = task['task'][:40] + '...' if len(task['task']) > 40 else task['task']
        
        table_data.append([
            task['task_id'][:8],
            task['agent_id'],
            task_desc,
            f"{status_emoji} {task['status']}",
            task.get('command', '')[:30] if task.get('command') else '-'
        ])
    
    headers = ['任务ID', 'Agent', '任务描述', '状态', '命令']
    print("\n" + tabulate_func(table_data, headers=headers, tablefmt='grid'))


def main():
    parser = argparse.ArgumentParser(description="Multi-Agent 客户端")
    parser.add_argument("--server", default="http://localhost:8000",
                       help="服务器地址")
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # agent 命令组
    agent_parser = subparsers.add_parser("agent", help="Agent 管理")
    agent_sub = agent_parser.add_subparsers(dest="action")
    
    # agent create
    create_parser = agent_sub.add_parser("create", help="创建 Agent")
    create_parser.add_argument("name", help="Agent 名称")
    create_parser.add_argument("role", choices=['monitor', 'security', 'network', 
                                                'database', 'devops', 'general'],
                              help="Agent 角色")
    create_parser.add_argument("--provider", default="deepseek",
                              choices=['deepseek', 'grok', 'openai', 'anthropic'],
                              help="LLM 提供商")
    create_parser.add_argument("--desc", help="描述")
    create_parser.add_argument("--model", help="模型名称（不指定则使用默认）")
    create_parser.add_argument("--api-key", help="API Key（不指定则使用环境变量）")
    
    # agent list
    list_parser = agent_sub.add_parser("list", help="列出 Agent")
    list_parser.add_argument("--role", help="按角色过滤")
    list_parser.add_argument("--status", help="按状态过滤")
    
    # agent get
    get_parser = agent_sub.add_parser("get", help="获取 Agent 详情")
    get_parser.add_argument("agent_id", help="Agent ID")
    
    # agent delete
    delete_parser = agent_sub.add_parser("delete", help="删除 Agent")
    delete_parser.add_argument("agent_id", help="Agent ID")
    
    # task 命令组
    task_parser = subparsers.add_parser("task", help="任务管理")
    task_sub = task_parser.add_subparsers(dest="action")
    
    # task submit
    submit_parser = task_sub.add_parser("submit", help="提交任务")
    submit_parser.add_argument("task", help="任务描述")
    submit_parser.add_argument("--agent", help="指定 Agent ID")
    submit_parser.add_argument("--role", help="指定角色")
    submit_parser.add_argument("--auto", action="store_true", help="自动确认")
    submit_parser.add_argument("--dry-run", action="store_true", help="仅生成命令")
    submit_parser.add_argument("--priority", type=int, default=0, help="优先级 0-10")
    
    # task list
    task_list_parser = task_sub.add_parser("list", help="列出任务")
    task_list_parser.add_argument("--agent", help="按 Agent 过滤")
    task_list_parser.add_argument("--status", help="按状态过滤")
    task_list_parser.add_argument("--limit", type=int, default=20, help="限制数量")
    
    # task get
    task_get_parser = task_sub.add_parser("get", help="获取任务详情")
    task_get_parser.add_argument("task_id", help="任务 ID")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    client = MultiAgentClient(args.server)
    
    try:
        if args.command == "agent":
            if args.action == "create":
                agent = client.create_agent(
                    args.name, args.role, 
                    llm_provider=args.provider,
                    description=args.desc, 
                    model_name=args.model,
                    api_key=args.api_key
                )
                print(f"✅ Agent 已创建: {agent['agent_id']}")
                print(f"   名称: {agent['name']}")
                print(f"   角色: {agent['role']}")
                print(f"   LLM: {agent['llm_provider']} ({agent['model_name']})")
                print(f"   能力: {', '.join(agent['capabilities'])}")
            
            elif args.action == "list":
                agents = client.list_agents(role=args.role, status=args.status)
                print_agents_table(agents)
            
            elif args.action == "get":
                agent = client.get_agent(args.agent_id)
                print(f"\n{'='*60}")
                print(f"Agent ID: {agent['agent_id']}")
                print(f"名称: {agent['name']}")
                print(f"角色: {agent['role']}")
                print(f"状态: {agent['status']}")
                print(f"描述: {agent['description']}")
                print(f"LLM 提供商: {agent['llm_provider']}")
                print(f"模型: {agent['model_name']}")
                print(f"当前任务: {agent['current_tasks']}/{agent['max_concurrent_tasks']}")
                print(f"已完成任务: {agent['total_tasks_completed']}")
                print(f"能力: {', '.join(agent['capabilities'])}")
                print(f"创建时间: {agent['created_at']}")
                print(f"最后活跃: {agent['last_active']}")
                print(f"{'='*60}\n")
            
            elif args.action == "delete":
                result = client.delete_agent(args.agent_id)
                print(f"✅ {result['message']}")
        
        elif args.command == "task":
            if args.action == "submit":
                task = client.submit_task(
                    args.task,
                    agent_id=args.agent,
                    role=args.role,
                    auto_confirm=args.auto,
                    dry_run=args.dry_run,
                    priority=args.priority
                )
                print(f"✅ 任务已提交: {task['task_id']}")
                print(f"   分配给: {task['agent_id']}")
                print(f"   状态: {task['status']}")
            
            elif args.action == "list":
                tasks = client.list_tasks(
                    agent_id=args.agent,
                    status=args.status,
                    limit=args.limit
                )
                print_tasks_table(tasks)
            
            elif args.action == "get":
                task = client.get_task(args.task_id)
                print(f"\n{'='*60}")
                print(f"任务 ID: {task['task_id']}")
                print(f"Agent: {task['agent_id']}")
                print(f"状态: {task['status']}")
                print(f"任务: {task['task']}")
                if task.get('command'):
                    print(f"命令: {task['command']}")
                if task.get('output'):
                    print(f"\n输出:\n{task['output']}")
                if task.get('error'):
                    print(f"\n错误:\n{task['error']}")
                if task.get('analysis'):
                    print(f"\n🤖 AI 分析:\n{task['analysis']}")
                print(f"创建时间: {task['created_at']}")
                if task.get('completed_at'):
                    print(f"完成时间: {task['completed_at']}")
                print(f"{'='*60}\n")
    
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到服务器: {args.server}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # 检查 tabulate 是否安装
    try:
        from tabulate import tabulate as tabulate_func
    except ImportError:
        print("请安装 tabulate: pip install tabulate")
        sys.exit(1)
    
    main()
