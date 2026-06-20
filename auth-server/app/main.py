"""西瓜短剧Agent —— 云端认证 / 升级 / 封号服务（单独部署在小服务器）。

职责（前期最小集，沿用"先注册登录、收费钩子预留"定稿）：
- 注册 / 登录 / 发 JWT
- 违规上报：累计红线违规，达阈值置 banned（封号权威在此）
- 版本检查（升级提醒）

密码用 stdlib pbkdf2 加盐哈希；JWT 用 PyJWT。无需额外编译依赖。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# ---------------- 配置 ----------------
BASE_DIR = Path(__file__).resolve().parents[1]
DB_URL = f"sqlite:///{(BASE_DIR / 'auth.db').as_posix()}"
SECRET = os.environ.get("XIGUA_AUTH_SECRET", "dev-secret-change-me-in-prod")
BAN_THRESHOLD = int(os.environ.get("XIGUA_BAN_THRESHOLD", "3"))
# 升级信息（后续接 OSS/对象存储下载地址）
LATEST_VERSION = os.environ.get("XIGUA_LATEST_VERSION", "0.1.0")
DOWNLOAD_URL = os.environ.get("XIGUA_DOWNLOAD_URL", "")

COMPLIANCE_DICTIONARY = {
    "red": ["云端占位红线词|演示", "云端占位禁用词|演示"],
    "yellow": ["云端占位黄线词|演示", "云端占位提醒词|演示"],
}
_dictionary_json = json.dumps(
    COMPLIANCE_DICTIONARY,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
COMPLIANCE_DICT_VERSION = os.environ.get(
    "XIGUA_COMPLIANCE_DICT_VERSION",
    hashlib.sha256(_dictionary_json).hexdigest()[:12],
)

engine = create_engine(DB_URL, connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(String(16), default="free")  # 收费钩子预留
    expire_at: Mapped[datetime | None] = mapped_column(DateTime)
    role: Mapped[str] = mapped_column(String(16), default="user")
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    banned_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------- 工具 ----------------
def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"{salt.hex()}:{dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def make_token(username: str) -> str:
    return jwt.encode({"sub": username, "iat": int(time.time())}, SECRET, algorithm="HS256")


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 token")
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1], SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "token 无效")
    user = db.scalars(select(User).where(User.username == payload.get("sub"))).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


# ---------------- DTO ----------------
class Credentials(BaseModel):
    username: str
    password: str


class ViolationReport(BaseModel):
    username: str
    level: str  # red / yellow


def user_view(u: User) -> dict:
    return {
        "username": u.username,
        "plan": u.plan,
        "role": u.role,
        "violation_count": u.violation_count,
        "banned": u.banned,
        "banned_reason": u.banned_reason,
    }


# ---------------- 应用 ----------------
app = FastAPI(title="西瓜短剧Agent · 认证服务", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # 本地任意端口（dev 多端口/预览）+ Tauri 壳
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/register")
def register(body: Credentials, db: Session = Depends(get_db)) -> dict:
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise HTTPException(409, "用户名已存在")
    user = User(username=body.username, password_hash=hash_pw(body.password))
    db.add(user)
    db.commit()
    return {"token": make_token(user.username), "user": user_view(user)}


@app.post("/auth/login")
def login(body: Credentials, db: Session = Depends(get_db)) -> dict:
    user = db.scalars(select(User).where(User.username == body.username)).first()
    if not user or not verify_pw(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if user.banned:
        raise HTTPException(403, detail={"banned": True, "message": "账号已封禁", "reason": user.banned_reason})
    return {"token": make_token(user.username), "user": user_view(user)}


@app.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return user_view(user)


@app.post("/compliance/violation")
def report_violation(body: ViolationReport, db: Session = Depends(get_db)) -> dict:
    """客户端命中红线后上报；封号权威判定在此。"""
    user = db.scalars(select(User).where(User.username == body.username)).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    if body.level == "red":
        user.violation_count += 1
        if user.violation_count >= BAN_THRESHOLD and not user.banned:
            user.banned = True
            user.banned_reason = f"红线违规累计达到 {BAN_THRESHOLD} 次"
    db.commit()
    return {
        "violation_count": user.violation_count,
        "banned": user.banned,
        "banned_reason": user.banned_reason,
    }


@app.get("/compliance/status")
def compliance_status(username: str, db: Session = Depends(get_db)) -> dict:
    user = db.scalars(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    return user_view(user)


@app.get("/compliance/dict")
def compliance_dictionary(since: str | None = None) -> dict:
    if since == COMPLIANCE_DICT_VERSION:
        return {
            "version": COMPLIANCE_DICT_VERSION,
            "encoding": "gzip+base64",
            "payload": None,
        }
    payload = base64.b64encode(gzip.compress(_dictionary_json, mtime=0)).decode("ascii")
    return {
        "version": COMPLIANCE_DICT_VERSION,
        "encoding": "gzip+base64",
        "payload": payload,
    }


@app.get("/version")
def version() -> dict:
    """升级检查：客户端比对 latest 与本地版本。"""
    return {"latest": LATEST_VERSION, "url": DOWNLOAD_URL, "notes": ""}
