#!/usr/bin/env python3
"""直接检查内存中的 app_deploys"""
import sys
sys.path.insert(0, '/Users/jatsmith/agentops/server')
import main

print(f"agents 数量: {len(main.agents)}")
print(f"tasks 数量: {len(main.tasks)}")
print(f"app_deploys 数量: {len(main.app_deploys)}")
print(f"app_deploys keys: {list(main.app_deploys.keys())}")

if main.app_deploys:
    for deploy_id, deploy in main.app_deploys.items():
        print(f"\n{deploy_id}:")
        print(f"  status: {deploy.status}")
        print(f"  owner: {deploy.owner}")
