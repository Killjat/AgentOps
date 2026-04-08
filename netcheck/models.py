"""网络检测数据模型"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from enum import Enum


class IPType(str, Enum):
    RESIDENTIAL = "residential"   # 住宅 IP
    DATACENTER  = "datacenter"    # 机房 IP
    PROXY       = "proxy"         # 代理/VPN
    MOBILE      = "mobile"        # 移动网络
    UNKNOWN     = "unknown"


class PathQuality(str, Enum):
    CLEAN   = "clean"    # 干净，无异常跳
    SUSPECT = "suspect"  # 可疑，有代理特征
    BAD     = "bad"      # 差，路径异常


class NodeResult(BaseModel):
    agent_id: str
    agent_name: str = ""

    # 出口 IP 信息
    exit_ip: str = ""
    ip_city: str = ""
    ip_region: str = ""
    ip_country: str = ""
    ip_org: str = ""       # ASN + 组织名
    ip_type: IPType = IPType.UNKNOWN

    # 路由路径
    traceroute_hops: List[str] = []
    traceroute_raw: str = ""

    # 延迟
    latency_ms: float = -1

    # AI 分析
    path_quality: PathQuality = PathQuality.CLEAN
    risk_score: int = 0        # 0-100，越高越危险
    analysis: str = ""         # AI 分析结论
    recommendation: str = ""   # 建议

    error: str = ""
    status: str = "pending"    # pending / running / success / failed


class CheckTask(BaseModel):
    task_id: str
    target: str                # 检测目标，如 tiktok.com
    agent_ids: List[str]
    status: str = "pending"
    results: List[NodeResult] = []
    summary: str = ""
    created_at: str = ""
    completed_at: str = ""


class CheckRequest(BaseModel):
    target: str
    agent_ids: List[str]
