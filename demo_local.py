#!/usr/bin/env python3
"""
本地演示脚本 - 不需要真实 API Key
模拟 LLM 响应来测试系统功能
"""

import asyncio
import json
from datetime import datetime

# 模拟的命令映射
MOCK_COMMANDS = {
    "查看系统开放端口": "ss -tuln | grep LISTEN",
    "查看磁盘使用情况": "df -h",
    "查看内存使用率": "free -h",
    "查看cpu使用率": "top -bn1 | grep 'Cpu(s)'",
    "查看进程": "ps aux | head -20",
    "查看网络连接": "netstat -tuln",
    "查看系统信息": "uname -a",
    "查看当前用户": "whoami",
    "查看当前目录": "pwd",
    "列出文件": "ls -la"
}

class MockAgent:
    """模拟 Agent"""
    
    def __init__(self, agent_id, name, role):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.tasks_completed = 0
    
    def generate_command(self, task):
        """模拟生成命令"""
        # 简单的关键词匹配
        for key, cmd in MOCK_COMMANDS.items():
            if key in task.lower():
                return cmd
        
        # 默认命令
        if "端口" in task:
            return "ss -tuln | grep LISTEN"
        elif "磁盘" in task:
            return "df -h"
        elif "内存" in task:
            return "free -h"
        elif "cpu" in task.lower():
            return "top -bn1 | grep 'Cpu(s)'"
        else:
            return "echo '未找到匹配的命令'"
    
    async def execute_task(self, task):
        """执行任务"""
        print(f"\n{'='*60}")
        print(f"🤖 Agent: {self.name} ({self.agent_id})")
        print(f"📝 任务: {task}")
        print(f"{'='*60}")
        
        # 模拟思考
        print("💭 正在分析任务...")
        await asyncio.sleep(0.5)
        
        # 生成命令
        command = self.generate_command(task)
        print(f"💡 生成的命令: {command}")
        
        # 模拟执行
        print("⚙️  正在执行...")
        await asyncio.sleep(0.3)
        
        # 执行真实命令
        import subprocess
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                output = result.stdout
                print(f"✅ 执行成功:\n")
                print(output[:500])  # 只显示前 500 字符
                if len(output) > 500:
                    print("... (输出已截断)")
                self.tasks_completed += 1
                return True, output
            else:
                error = result.stderr
                print(f"❌ 执行失败:\n{error}")
                return False, error
        except Exception as e:
            print(f"❌ 执行失败: {e}")
            return False, str(e)

async def demo():
    """演示主函数"""
    print("=" * 60)
    print("  Linux Agent 本地演示")
    print("  (模拟模式 - 不需要 API Key)")
    print("=" * 60)
    
    # 创建模拟 Agent
    agents = [
        MockAgent("agent-001", "监控专家-01", "monitor"),
        MockAgent("agent-002", "安全专家-01", "security"),
        MockAgent("agent-003", "网络专家-01", "network"),
    ]
    
    print(f"\n✅ 已创建 {len(agents)} 个 Agent:")
    for agent in agents:
        print(f"   - {agent.agent_id}: {agent.name} ({agent.role})")
    
    # 测试任务
    tasks = [
        "查看系统开放端口",
        "查看磁盘使用情况",
        "查看内存使用率",
    ]
    
    print(f"\n📋 准备执行 {len(tasks)} 个任务\n")
    await asyncio.sleep(1)
    
    # 执行任务
    for i, task in enumerate(tasks):
        agent = agents[i % len(agents)]
        await agent.execute_task(task)
        await asyncio.sleep(0.5)
    
    # 总结
    print("\n" + "=" * 60)
    print("  演示总结")
    print("=" * 60)
    
    total_tasks = sum(agent.tasks_completed for agent in agents)
    print(f"\n✅ 总共完成 {total_tasks} 个任务")
    
    for agent in agents:
        print(f"   {agent.name}: {agent.tasks_completed} 个任务")
    
    print("\n" + "=" * 60)
    print("  下一步")
    print("=" * 60)
    print("\n这是本地模拟演示。要使用完整功能:")
    print("\n1. 获取 DeepSeek API Key:")
    print("   访问 https://platform.deepseek.com/")
    print("\n2. 配置 API Key:")
    print("   export DEEPSEEK_API_KEY='your-api-key'")
    print("\n3. 启动真实服务器:")
    print("   python linux_agent_multi.py")
    print("\n4. 使用客户端:")
    print("   python linux_agent_multi_client.py agent list")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(demo())
