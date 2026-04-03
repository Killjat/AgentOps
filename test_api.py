#!/usr/bin/env python3
"""测试部署数据是否被正确加载"""
import requests

response = requests.get('http://localhost:8000/deploy/app')
print(f"部署列表: {response.json()}")
print(f"数量: {len(response.json())}")

# 检查单个部署
if len(response.json()) > 0:
    deploy_id = response.json()[0]['deploy_id']
    response2 = requests.get(f'http://localhost:8000/deploy/app/{deploy_id}')
    print(f"\n单个部署详情: {response2.json()}")
else:
    print("\n没有部署记录")

# 检查 agents
response3 = requests.get('http://localhost:8000/agents')
print(f"\nAgents: {response3.json()}")
