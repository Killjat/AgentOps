"""服务器管理路由"""
import uuid
from datetime import datetime
from typing import Optional

import asyncssh
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from models import ServerInfo, OSType
from core.state import servers, agents
from core.storage import _save_servers_yaml
from routers.auth import _check_perm, _get_caller, _is_admin

router = APIRouter(prefix="/servers", tags=["servers"])


class ServerEntry(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    os_type: Optional[str] = None


@router.get("")
async def list_servers(authorization: str = Header(default="")):
    """获取服务器列表：admin 看全部，其他用户只看自己的"""
    caller = _get_caller(authorization)
    if _is_admin(authorization):
        return [s.model_dump(exclude_none=True) for s in servers.values()]
    return [s.model_dump(exclude_none=True) for s in servers.values() if s.owner == caller]


@router.post("")
async def add_server(entry: ServerEntry, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    caller = _get_caller(authorization)

    for existing in servers.values():
        if existing.host == entry.host and existing.port == entry.port and existing.username == entry.username:
            existing.name = entry.name
            if entry.password:
                existing.password = entry.password
            if entry.ssh_key:
                existing.ssh_key = entry.ssh_key
            _save_servers_yaml()
            return {"message": "服务器已存在，已更新信息", "server_id": existing.server_id}

    server_id = f"server-{uuid.uuid4().hex[:8]}"
    server = ServerInfo(
        server_id=server_id,
        name=entry.name,
        host=entry.host,
        port=entry.port,
        username=entry.username,
        password=entry.password,
        ssh_key=entry.ssh_key,
        os_type=OSType(entry.os_type) if entry.os_type else OSType.UNKNOWN,
        owner=caller,
        created_at=datetime.now().isoformat()
    )
    servers[server_id] = server
    _save_servers_yaml()
    return {"message": "添加成功", "server_id": server_id}


@router.put("/{server_id}")
async def update_server(server_id: str, entry: ServerEntry, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    if server_id not in servers:
        raise HTTPException(status_code=404, detail="服务器不存在")

    server = servers[server_id]
    if not _is_admin(authorization) and server.owner != _get_caller(authorization):
        raise HTTPException(status_code=403, detail="无权修改他人的服务器")

    server.name = entry.name
    server.host = entry.host
    server.port = entry.port
    server.username = entry.username
    server.password = entry.password
    server.ssh_key = entry.ssh_key
    if entry.os_type:
        server.os_type = OSType(entry.os_type)

    _save_servers_yaml()
    return {"message": "更新成功"}


@router.delete("/{server_id}")
async def delete_server(server_id: str, authorization: str = Header(default="")):
    _check_perm(authorization, "login")
    if server_id not in servers:
        raise HTTPException(status_code=404, detail="服务器不存在")

    server = servers[server_id]
    if not _is_admin(authorization) and server.owner != _get_caller(authorization):
        raise HTTPException(status_code=403, detail="无权删除他人的服务器")

    for agent in agents.values():
        if agent.server_id == server_id:
            raise HTTPException(
                status_code=400,
                detail=f"此服务器有 {len([a for a in agents.values() if a.server_id == server_id])} 个 Agent 依赖，请先删除 Agent"
            )

    del servers[server_id]
    _save_servers_yaml()
    return {"message": "删除成功"}


@router.post("/test")
async def test_server(entry: ServerEntry):
    """测试 SSH 连接是否可用"""
    kwargs = dict(host=entry.host, port=entry.port, username=entry.username,
                  known_hosts=None, preferred_auth="password,keyboard-interactive")
    if entry.password:
        kwargs["password"] = entry.password
    if entry.ssh_key:
        kwargs["client_keys"] = [entry.ssh_key]
    try:
        conn = await asyncssh.connect(**kwargs)
        r = await conn.run("uname -sr || ver", check=False)
        conn.close()
        return {"ok": True, "info": r.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
