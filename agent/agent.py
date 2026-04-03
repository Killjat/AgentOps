#!/usr/bin/env python3
"""
兼容入口 - 保持向后兼容
新代码请使用 python3 -m agent 启动
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.__main__ import main

if __name__ == "__main__":
    main()
