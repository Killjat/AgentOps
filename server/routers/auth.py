"""用户认证与权限路由"""
import os
import secrets
import hashlib
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

USERS_FILE = Path(__file__).parent.parent.parent / "users.json"

# token -> {username, role}
# role: admin | user | guest
_sessions: dict = {}


# ── 模型 ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class GrantRequest(BaseModel):
    username: str
    perms: List[str]


# ── 工具函数 ─────────────────────────────────────────────────────

def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text())


def _save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2))


def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _guest_id(ip: str) -> str:
    """根据 IP 生成唯一游客 ID"""
    return "guest-" + hashlib.md5(ip.encode()).hexdigest()[:8]


def _get_session(authorization: str) -> Optional[dict]:
    token = authorization.replace("Bearer ", "")
    return _sessions.get(token)


def _get_caller(authorization: str) -> str:
    """获取当前用户名（未登录返回空字符串）"""
    s = _get_session(authorization)
    return s["username"] if s else ""


def _is_admin(authorization: str) -> bool:
    s = _get_session(authorization)
    return s is not None and s.get("role") == "admin"


def _check_perm(authorization: str, perm: str):
    s = _get_session(authorization)
    if not s:
        raise HTTPException(status_code=401, detail="请先登录")
    if perm == "admin" and s["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _check_owner(authorization: str, owner: str, resource: str = "资源"):
    """检查是否是 owner 或 admin"""
    s = _get_session(authorization)
    if not s:
        raise HTTPException(status_code=401, detail="请先登录")
    if s["role"] == "admin":
        return  # admin 全部放行
    if s["username"] != owner:
        raise HTTPException(status_code=403, detail=f"无权操作他人的{resource}")


# ── 路由 ─────────────────────────────────────────────────────────

@router.post("/guest")
async def guest_login(request: Request):
    """游客登录，按 IP 生成唯一 ID"""
    ip = request.client.host
    guest_id = _guest_id(ip)
    token = secrets.token_hex(32)
    _sessions[token] = {"username": guest_id, "role": "guest"}
    return {"token": token, "username": guest_id, "role": "guest", "perms": []}


@router.post("/register")
async def register(req: RegisterRequest):
    if len(req.username) < 2 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="用户名至少2位，密码至少4位")
    users = _load_users()
    if req.username in users:
        raise HTTPException(status_code=400, detail="用户名已存在")
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    if req.username == admin_user:
        raise HTTPException(status_code=400, detail="不能使用该用户名")
    users[req.username] = {"password": _hash_pw(req.password), "perms": []}
    _save_users(users)
    return {"ok": True, "message": "注册成功"}


@router.post("/login")
async def login(req: LoginRequest):
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "admin123")
    if req.username == admin_user and req.password == admin_pass:
        token = secrets.token_hex(32)
        _sessions[token] = {"username": req.username, "role": "admin"}
        return {"token": token, "username": req.username, "role": "admin", "perms": ["task", "host"]}
    users = _load_users()
    u = users.get(req.username)
    if u and u["password"] == _hash_pw(req.password):
        token = secrets.token_hex(32)
        _sessions[token] = {"username": req.username, "role": "user"}
        return {"token": token, "username": req.username, "role": "user", "perms": ["task", "host"]}
    raise HTTPException(status_code=401, detail="用户名或密码错误")


@router.post("/logout")
async def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "")
    _sessions.pop(token, None)
    return {"ok": True}


@router.get("/users")
async def list_users(authorization: str = Header(default="")):
    _check_perm(authorization, "admin")
    users = _load_users()
    return [{"username": k} for k in users.keys()]
