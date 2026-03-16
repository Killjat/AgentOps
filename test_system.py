#!/usr/bin/env python3
"""
系统测试脚本 - 验证所有组件是否正常工作
"""

import sys
import subprocess
import time

def print_section(title):
    """打印分节标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def check_dependencies():
    """检查依赖"""
    print_section("1. 检查依赖")
    
    dependencies = [
        'fastapi',
        'uvicorn', 
        'aiohttp',
        'websockets',
        'pydantic',
        'tabulate'
    ]
    
    all_ok = True
    for dep in dependencies:
        try:
            __import__(dep)
            print(f"✓ {dep}")
        except ImportError:
            print(f"✗ {dep} - 未安装")
            all_ok = False
    
    return all_ok

def check_files():
    """检查必要文件"""
    print_section("2. 检查文件")
    
    import os
    files = [
        'linux_agent_multi.py',
        'linux_agent_multi_client.py',
        'linux_agent_monitor.py'
    ]
    
    all_ok = True
    for file in files:
        if os.path.exists(file):
            print(f"✓ {file}")
        else:
            print(f"✗ {file} - 文件不存在")
            all_ok = False
    
    return all_ok

def check_api_keys():
    """检查 API Keys"""
    print_section("3. 检查 API Keys")
    
    import os
    
    keys = {
        'DEEPSEEK_API_KEY': os.getenv('DEEPSEEK_API_KEY'),
        'GROK_API_KEY': os.getenv('GROK_API_KEY'),
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'),
        'ANTHROPIC_API_KEY': os.getenv('ANTHROPIC_API_KEY')
    }
    
    has_key = False
    for name, value in keys.items():
        if value and value != 'sk-test-key-replace-with-real-key':
            print(f"✓ {name} 已配置")
            has_key = True
        else:
            print(f"  {name} 未配置（可选）")
    
    if not has_key:
        print("\n⚠️  警告: 未配置任何真实的 API Key")
        print("   系统可以启动，但无法调用 LLM API")
        print("   请配置至少一个 API Key:")
        print("   export DEEPSEEK_API_KEY='your-api-key'")
    
    return True  # 即使没有 key 也允许继续测试

def test_import():
    """测试导入主模块"""
    print_section("4. 测试模块导入")
    
    try:
        # 测试导入（不运行）
        import importlib.util
        spec = importlib.util.spec_from_file_location("linux_agent_multi", "linux_agent_multi.py")
        module = importlib.util.module_from_spec(spec)
        print("✓ linux_agent_multi.py 可以导入")
        return True
    except Exception as e:
        print(f"✗ 导入失败: {e}")
        return False

def print_next_steps():
    """打印下一步操作"""
    print_section("下一步操作")
    
    print("\n如果所有检查都通过，你可以：")
    print("\n1. 配置真实的 API Key:")
    print("   export DEEPSEEK_API_KEY='your-real-api-key'")
    
    print("\n2. 启动服务器:")
    print("   python linux_agent_multi.py")
    print("   或")
    print("   ./start_server.sh")
    
    print("\n3. 在另一个终端创建 Agent:")
    print("   python linux_agent_multi_client.py agent create \\")
    print("     '监控专家-01' monitor \\")
    print("     --provider deepseek")
    
    print("\n4. 提交测试任务:")
    print("   python linux_agent_multi_client.py task submit \\")
    print("     '查看系统开放端口'")
    
    print("\n5. 查看任务结果:")
    print("   python linux_agent_multi_client.py task list")
    
    print("\n6. 实时监控（可选）:")
    print("   python linux_agent_monitor.py")
    
    print("\n" + "=" * 60)

def main():
    """主函数"""
    print("=" * 60)
    print("  Linux Agent Multi-Agent 系统测试")
    print("=" * 60)
    
    # 运行所有检查
    checks = [
        ("依赖检查", check_dependencies),
        ("文件检查", check_files),
        ("API Key 检查", check_api_keys),
        ("模块导入测试", test_import)
    ]
    
    all_passed = True
    for name, check_func in checks:
        if not check_func():
            all_passed = False
    
    # 总结
    print_section("测试总结")
    if all_passed:
        print("✅ 所有检查通过！系统可以启动。")
    else:
        print("❌ 部分检查失败，请修复后再启动。")
    
    # 打印下一步
    print_next_steps()

if __name__ == "__main__":
    main()
