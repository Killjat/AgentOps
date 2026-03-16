#!/usr/bin/env python3
"""
Linux 服务器运维 Agent - 最小化原型
支持 DeepSeek API 和本地模型
"""

import subprocess
import json
import sys
from typing import Optional, Tuple
import re

# 配置
DEEPSEEK_API_KEY = "your-api-key-here"  # 替换为你的 API Key
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# 系统 Prompt
SYSTEM_PROMPT = """你是一个 Linux 服务器运维专家助手。
用户会用自然语言描述需求，你需要生成对应的 Linux 命令。

规则：
1. 只返回可执行的命令，不要任何解释
2. 如果需要多个命令，用 && 连接
3. 优先使用安全的命令
4. 如果任务不明确，返回 "NEED_CLARIFICATION: <问题>"

示例：
用户：查看系统开放端口
助手：ss -tuln | grep LISTEN

用户：查看 CPU 使用率
助手：top -bn1 | grep "Cpu(s)"

用户：查找占用内存最多的进程
助手：ps aux --sort=-%mem | head -n 10
"""

# 危险命令黑名单
DANGEROUS_PATTERNS = [
    r'rm\s+-rf\s+/',
    r'dd\s+if=/dev/zero',
    r'mkfs\.',
    r'chmod\s+-R\s+777\s+/',
    r':\(\)\{.*\}',  # fork bomb
]

# 需要确认的命令
CONFIRM_COMMANDS = ['rm', 'kill', 'pkill', 'reboot', 'shutdown', 'halt']


class LinuxAgent:
    """Linux 运维 Agent"""
    
    def __init__(self, api_key: str = DEEPSEEK_API_KEY):
        self.api_key = api_key
        
    def call_deepseek(self, user_input: str) -> str:
        """调用 DeepSeek API 生成命令"""
        try:
            import requests
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_input}
                ],
                "temperature": 0.1,
                "max_tokens": 200
            }
            
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data)
            response.raise_for_status()
            
            result = response.json()
            command = result['choices'][0]['message']['content'].strip()
            return command
            
        except ImportError:
            print("❌ 需要安装 requests: pip install requests")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 调用 DeepSeek API 失败: {e}")
            sys.exit(1)
    
    def check_safety(self, command: str) -> Tuple[bool, str]:
        """安全检查"""
        # 检查危险命令
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                return False, f"危险命令被拦截: {pattern}"
        
        # 检查需要确认的命令
        for cmd in CONFIRM_COMMANDS:
            if cmd in command.split()[0]:
                return False, f"需要用户确认: {cmd}"
        
        return True, "安全检查通过"
    
    def execute_command(self, command: str) -> Tuple[bool, str]:
        """执行命令"""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            output = result.stdout if result.stdout else result.stderr
            success = result.returncode == 0
            
            return success, output
            
        except subprocess.TimeoutExpired:
            return False, "命令执行超时（30秒）"
        except Exception as e:
            return False, f"执行失败: {e}"
    
    def run(self, user_input: str, auto_confirm: bool = False):
        """主执行流程"""
        print(f"\n🤖 Agent: 正在分析任务...")
        print(f"📝 任务: {user_input}")
        
        # 1. 调用 DeepSeek 生成命令
        command = self.call_deepseek(user_input)
        print(f"\n💡 生成的命令: {command}")
        
        # 检查是否需要澄清
        if command.startswith("NEED_CLARIFICATION:"):
            print(f"\n❓ {command}")
            return
        
        # 2. 安全检查
        is_safe, message = self.check_safety(command)
        print(f"🔒 安全检查: {message}")
        
        if not is_safe:
            if not auto_confirm:
                confirm = input(f"\n⚠️  {message}\n是否继续执行? (yes/no): ")
                if confirm.lower() != 'yes':
                    print("❌ 已取消执行")
                    return
        
        # 3. 执行命令
        print(f"\n⚙️  正在执行...")
        success, output = self.execute_command(command)
        
        # 4. 显示结果
        if success:
            print(f"\n✅ 执行成功:\n")
            print(output)
        else:
            print(f"\n❌ 执行失败:\n")
            print(output)


def main():
    """主函数"""
    print("=" * 60)
    print("🐧 Linux 服务器运维 Agent")
    print("基于 DeepSeek 的智能命令生成")
    print("=" * 60)
    
    # 检查 API Key
    if DEEPSEEK_API_KEY == "your-api-key-here":
        print("\n⚠️  请先配置 DEEPSEEK_API_KEY")
        print("编辑脚本，将 DEEPSEEK_API_KEY 替换为你的 API Key")
        print("\n或者使用环境变量:")
        print("export DEEPSEEK_API_KEY='your-key'")
        sys.exit(1)
    
    agent = LinuxAgent()
    
    # 交互模式
    if len(sys.argv) == 1:
        print("\n💬 交互模式 (输入 'exit' 退出)")
        while True:
            try:
                user_input = input("\n👤 你: ").strip()
                if user_input.lower() in ['exit', 'quit', 'q']:
                    print("👋 再见!")
                    break
                if not user_input:
                    continue
                agent.run(user_input)
            except KeyboardInterrupt:
                print("\n\n👋 再见!")
                break
    else:
        # 命令行模式
        user_input = " ".join(sys.argv[1:])
        agent.run(user_input)


if __name__ == "__main__":
    main()
