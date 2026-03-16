#!/usr/bin/env python3
"""
Linux 服务器运维 Agent - 支持本地 DeepSeek 模型
使用 Ollama 或 vLLM 运行本地模型
"""

import subprocess
import sys
import json
from typing import Optional, Tuple
import re

SYSTEM_PROMPT = """你是一个 Linux 服务器运维专家助手。
用户会用自然语言描述需求，你需要生成对应的 Linux 命令。

规则：
1. 只返回可执行的命令，不要任何解释
2. 如果需要多个命令，用 && 连接
3. 优先使用安全的命令

示例：
用户：查看系统开放端口
助手：ss -tuln | grep LISTEN

用户：查看 CPU 使用率  
助手：top -bn1 | grep "Cpu(s)"
"""

DANGEROUS_PATTERNS = [
    r'rm\s+-rf\s+/',
    r'dd\s+if=/dev/zero',
    r'mkfs\.',
]

CONFIRM_COMMANDS = ['rm', 'kill', 'reboot', 'shutdown']


class LocalLinuxAgent:
    """使用本地模型的 Linux Agent"""
    
    def __init__(self, model_type: str = "ollama", model_name: str = "deepseek-r1:7b"):
        """
        初始化 Agent
        
        Args:
            model_type: 模型类型 (ollama, vllm, openai-compatible)
            model_name: 模型名称
        """
        self.model_type = model_type
        self.model_name = model_name
        
    def call_ollama(self, user_input: str) -> str:
        """调用 Ollama 本地模型"""
        try:
            import requests
            
            url = "http://localhost:11434/api/chat"
            data = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_input}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 200
                }
            }
            
            response = requests.post(url, json=data, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            command = result['message']['content'].strip()
            
            # 清理可能的思考过程标记
            if '<think>' in command:
                command = re.sub(r'<think>.*?</think>', '', command, flags=re.DOTALL)
            command = command.strip()
            
            return command
            
        except requests.exceptions.ConnectionError:
            print("❌ 无法连接到 Ollama，请确保 Ollama 正在运行")
            print("启动 Ollama: ollama serve")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 调用 Ollama 失败: {e}")
            sys.exit(1)
    
    def call_openai_compatible(self, user_input: str, base_url: str, api_key: str = "dummy") -> str:
        """调用 OpenAI 兼容的 API (vLLM, LocalAI 等)"""
        try:
            import requests
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_input}
                ],
                "temperature": 0.1,
                "max_tokens": 200
            }
            
            response = requests.post(f"{base_url}/v1/chat/completions", 
                                    headers=headers, json=data, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            command = result['choices'][0]['message']['content'].strip()
            return command
            
        except Exception as e:
            print(f"❌ 调用 API 失败: {e}")
            sys.exit(1)
    
    def generate_command(self, user_input: str, **kwargs) -> str:
        """生成命令（根据模型类型选择方法）"""
        if self.model_type == "ollama":
            return self.call_ollama(user_input)
        elif self.model_type == "openai-compatible":
            base_url = kwargs.get("base_url", "http://localhost:8000")
            api_key = kwargs.get("api_key", "dummy")
            return self.call_openai_compatible(user_input, base_url, api_key)
        else:
            raise ValueError(f"不支持的模型类型: {self.model_type}")
    
    def check_safety(self, command: str) -> Tuple[bool, str]:
        """安全检查"""
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                return False, f"危险命令被拦截"
        
        for cmd in CONFIRM_COMMANDS:
            if command.split()[0] == cmd:
                return False, f"需要用户确认"
        
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
    
    def run(self, user_input: str, dry_run: bool = False, auto_confirm: bool = False, **kwargs):
        """主执行流程"""
        print(f"\n🤖 Agent: 正在分析任务...")
        print(f"📝 任务: {user_input}")
        print(f"🔧 模型: {self.model_type} ({self.model_name})")
        
        # 1. 生成命令
        command = self.generate_command(user_input, **kwargs)
        print(f"\n💡 生成的命令: {command}")
        
        if dry_run:
            print("\n🔍 Dry-run 模式，不执行命令")
            return
        
        # 2. 安全检查
        is_safe, message = self.check_safety(command)
        print(f"🔒 安全检查: {message}")
        
        if not is_safe and not auto_confirm:
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
    import argparse
    
    parser = argparse.ArgumentParser(description="Linux 服务器运维 Agent (本地模型)")
    parser.add_argument("task", nargs="*", help="要执行的任务")
    parser.add_argument("--model-type", default="ollama", 
                       choices=["ollama", "openai-compatible"],
                       help="模型类型")
    parser.add_argument("--model-name", default="deepseek-r1:7b",
                       help="模型名称")
    parser.add_argument("--base-url", default="http://localhost:8000",
                       help="API base URL (for openai-compatible)")
    parser.add_argument("--api-key", default="dummy",
                       help="API key (for openai-compatible)")
    parser.add_argument("--dry-run", action="store_true",
                       help="只生成命令，不执行")
    parser.add_argument("--auto", action="store_true",
                       help="自动执行，跳过确认")
    parser.add_argument("--interactive", "-i", action="store_true",
                       help="交互模式")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🐧 Linux 服务器运维 Agent (本地模型版)")
    print("=" * 60)
    
    agent = LocalLinuxAgent(
        model_type=args.model_type,
        model_name=args.model_name
    )
    
    kwargs = {
        "base_url": args.base_url,
        "api_key": args.api_key
    }
    
    # 交互模式
    if args.interactive or not args.task:
        print("\n💬 交互模式 (输入 'exit' 退出)")
        print(f"🔧 使用模型: {args.model_type} ({args.model_name})")
        
        while True:
            try:
                user_input = input("\n👤 你: ").strip()
                if user_input.lower() in ['exit', 'quit', 'q']:
                    print("👋 再见!")
                    break
                if not user_input:
                    continue
                agent.run(user_input, dry_run=args.dry_run, 
                         auto_confirm=args.auto, **kwargs)
            except KeyboardInterrupt:
                print("\n\n👋 再见!")
                break
    else:
        # 命令行模式
        user_input = " ".join(args.task)
        agent.run(user_input, dry_run=args.dry_run, 
                 auto_confirm=args.auto, **kwargs)


if __name__ == "__main__":
    main()
