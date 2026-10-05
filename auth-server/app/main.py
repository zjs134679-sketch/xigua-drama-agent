"""西瓜短剧Agent —— 云端认证 / 授权加密 / 升级 / 封号服务。

职责：
- 注册 / 登录 / 发 JWT
- 卡密激活 + 机器码绑定 + plan/到期校验（软件授权加密核心）
- 违规上报：累计红线违规，达阈值置 banned
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
import secrets
import string
import time
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, event, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# ---------------- 配置 ----------------
BASE_DIR = Path(__file__).resolve().parents[1]
_AUTH_DB = Path(os.environ.get("XIGUA_AUTH_DB", str(BASE_DIR / "auth.db")))
DB_URL = f"sqlite:///{_AUTH_DB.as_posix()}"
SECRET = os.environ.get("XIGUA_AUTH_SECRET", "dev-secret-change-me-in-prod")
ADMIN_SECRET = os.environ.get("XIGUA_ADMIN_SECRET", SECRET)
BAN_THRESHOLD = int(os.environ.get("XIGUA_BAN_THRESHOLD", "3"))
TRIAL_DAYS = int(os.environ.get("XIGUA_TRIAL_DAYS", "7"))
DEFAULT_MAX_MACHINES = int(os.environ.get("XIGUA_MAX_MACHINES", "2"))
# 1=强制授权（无有效 plan/到期不可用）；0=仅登录不卡功能（开发兼容）
LICENSE_REQUIRED = os.environ.get("XIGUA_LICENSE_REQUIRED", "1").strip() not in ("0", "false", "False")
LATEST_VERSION = os.environ.get("XIGUA_LATEST_VERSION", "0.1.0")
DOWNLOAD_URL = os.environ.get("XIGUA_DOWNLOAD_URL", "")
# 工业级加固参数（客户端仍可被逆向；真正控权在服务端）
JWT_TTL_SECONDS = int(os.environ.get("XIGUA_JWT_TTL_SECONDS", str(7 * 24 * 3600)))
CAP_TTL_SECONDS = int(os.environ.get("XIGUA_CAP_TTL_SECONDS", "1800"))  # 能力票 30 分钟
PBKDF2_ITERS = int(os.environ.get("XIGUA_PBKDF2_ITERS", "600000"))
LICENSE_PEPPER = os.environ.get("XIGUA_LICENSE_PEPPER", SECRET)
if SECRET == "dev-secret-change-me-in-prod":
    import warnings

    warnings.warn("XIGUA_AUTH_SECRET 仍为默认值，生产环境必须更换为长随机串", stacklevel=1)

_SECRET_DICT_DIR = BASE_DIR / "secret_dict"
_PLACEHOLDER = {
    "red": ["云端占位红线词|演示", "云端占位禁用词|演示"],
    "yellow": ["云端占位黄线词|演示", "云端占位提醒词|演示"],
}


def _load_words(level: str) -> list[str]:
    path = _SECRET_DICT_DIR / f"{level}.txt"
    if not path.exists():
        return _PLACEHOLDER[level]
    words = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return words or _PLACEHOLDER[level]


COMPLIANCE_DICTIONARY = {"red": _load_words("red"), "yellow": _load_words("yellow")}
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

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_on_connect(dbapi_conn, _connection_record) -> None:  # type: ignore[no-untyped-def]
    """WAL + busy_timeout，避免管理端并发读写时锁库导致掉线。"""
    try:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
    except Exception:
        pass


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(String(16), default="trial")  # trial / free / pro / studio
    expire_at: Mapped[datetime | None] = mapped_column(DateTime)
    role: Mapped[str] = mapped_column(String(16), default="user")
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    banned_reason: Mapped[str | None] = mapped_column(Text)
    machines: Mapped[str | None] = mapped_column(Text, default="[]")  # JSON list of machine_id
    max_machines: Mapped[int] = mapped_column(Integer, default=2)
    # 用过的卡密记录（JSON 数组）+ 最近一次
    last_license_code: Mapped[str | None] = mapped_column(String(64))
    license_history: Mapped[str | None] = mapped_column(Text, default="[]")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)  # 最近联网
    last_machine_id: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)  # 管理员备注（客户名/订单等）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LicenseKey(Base):
    """卡密：发卡后用户激活写入 plan/expire_at，并绑定机器。"""

    __tablename__ = "license_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(16), default="pro")
    days: Mapped[int] = mapped_column(Integer, default=365)
    max_machines: Mapped[int] = mapped_column(Integer, default=2)
    note: Mapped[str | None] = mapped_column(Text)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by: Mapped[str | None] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuthEvent(Base):
    """联网监控：注册/登录/激活/心跳等事件流水。"""

    __tablename__ = "auth_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    machine_id: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# ---------------- 工具 ----------------
def hash_pw(pw: str) -> str:
    """PBKDF2-HMAC-SHA256（默认 60 万次迭代）+ 16 字节盐。"""
    salt = os.urandom(16)
    iters = PBKDF2_ITERS
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters)
    return f"{salt.hex()}:{dk.hex()}:{iters}"


def verify_pw(pw: str, stored: str) -> bool:
    """兼容旧格式 salt:dk（固定 20 万次）与新格式 salt:dk:iters。"""
    try:
        parts = stored.split(":")
        if len(parts) == 2:
            salt_hex, dk_hex = parts
            iters = 200_000
        elif len(parts) == 3:
            salt_hex, dk_hex, iters_s = parts
            iters = int(iters_s)
        else:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), iters
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def make_token(username: str, machine_id: str | None = None) -> str:
    """访问令牌：含 exp / jti / 可选机器绑定 mid。"""
    now = int(time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
        "jti": secrets.token_hex(16),
        "typ": "access",
    }
    mid = (machine_id or "").strip()
    if mid:
        payload["mid"] = mid[:128]
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    return token if isinstance(token, str) else token.decode("utf-8")


def make_capability(user: "User", machine_id: str | None = None) -> str | None:
    """短期能力票：本地后端可离线验签（共享 SECRET）。"""
    if not is_license_active(user):
        return None
    now = int(time.time())
    mid = (machine_id or "").strip()[:128]
    payload = {
        "sub": user.username,
        "iat": now,
        "exp": now + CAP_TTL_SECONDS,
        "jti": secrets.token_hex(12),
        "typ": "capability",
        "plan": user.plan,
        "lic": True,
        "mid": mid or None,
        "eat": int(user.expire_at.timestamp()) if user.expire_at else None,
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    return token if isinstance(token, str) else token.decode("utf-8")


def license_code_digest(code: str) -> str:
    """卡密指纹（HMAC），用于日志脱敏。"""
    raw = code.strip().upper().encode("utf-8")
    return hmac.new(LICENSE_PEPPER.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:16]


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(
    authorization: str | None = Header(default=None),
    x_machine_id: str | None = Header(default=None, alias="X-Machine-Id"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 token")
    try:
        payload = jwt.decode(
            authorization.split(" ", 1)[1],
            SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(401, "token 无效")
    if payload.get("typ") == "capability":
        raise HTTPException(401, "请使用访问令牌，而非能力票")
    user = db.scalars(select(User).where(User.username == payload.get("sub"))).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    token_mid = (payload.get("mid") or "").strip()
    req_mid = (x_machine_id or "").strip()
    if token_mid and req_mid and token_mid != req_mid:
        raise HTTPException(401, "令牌与当前设备不匹配，请重新登录")
    return user


def require_admin(x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret")) -> None:
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret, ADMIN_SECRET):
        raise HTTPException(403, "管理员密钥无效")


def _parse_machines(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    return []


def _save_machines(user: User, machines: list[str]) -> None:
    user.machines = json.dumps(machines, ensure_ascii=False)


def is_license_active(user: User) -> bool:
    if user.banned:
        return False
    if not LICENSE_REQUIRED:
        return True
    if user.expire_at is None:
        return False
    return user.expire_at >= datetime.utcnow()


def license_reason(user: User) -> str | None:
    if user.banned:
        return user.banned_reason or "账号已封禁"
    if not LICENSE_REQUIRED:
        return None
    if user.expire_at is None:
        return "未激活授权，请输入卡密激活"
    if user.expire_at < datetime.utcnow():
        return f"授权已于 {user.expire_at.strftime('%Y-%m-%d')} 过期，请续费激活"
    return None


def bind_machine(user: User, machine_id: str | None) -> None:
    """绑定机器码；超出 max_machines 则拒绝。"""
    mid = (machine_id or "").strip()
    if not mid:
        return
    machines = _parse_machines(user.machines)
    if mid in machines:
        return
    limit = user.max_machines or DEFAULT_MAX_MACHINES
    if len(machines) >= limit:
        raise HTTPException(
            403,
            detail={
                "license": True,
                "message": f"已绑定 {len(machines)} 台设备（上限 {limit}），请先解绑或联系客服",
                "machines_bound": len(machines),
                "max_machines": limit,
            },
        )
    machines.append(mid)
    _save_machines(user, machines)


def ensure_machine_allowed(user: User, machine_id: str | None, *, auto_bind: bool = True) -> None:
    mid = (machine_id or "").strip()
    if not mid:
        return
    machines = _parse_machines(user.machines)
    if mid in machines:
        return
    if not machines and auto_bind:
        bind_machine(user, mid)
        return
    if auto_bind and len(machines) < (user.max_machines or DEFAULT_MAX_MACHINES):
        bind_machine(user, mid)
        return
    raise HTTPException(
        403,
        detail={
            "license": True,
            "message": "当前设备未授权，请使用已绑定设备或联系客服重置机器码",
            "machines_bound": len(machines),
            "max_machines": user.max_machines or DEFAULT_MAX_MACHINES,
        },
    )


def gen_license_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "XG-" + "-".join(parts)


def _migrate_schema() -> None:
    """为已有 SQLite 补列，避免 create_all 不改旧表。"""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        if cols:
            alters = {
                "machines": "ALTER TABLE users ADD COLUMN machines TEXT DEFAULT '[]'",
                "max_machines": "ALTER TABLE users ADD COLUMN max_machines INTEGER DEFAULT 2",
                "last_license_code": "ALTER TABLE users ADD COLUMN last_license_code VARCHAR(64)",
                "license_history": "ALTER TABLE users ADD COLUMN license_history TEXT DEFAULT '[]'",
                "last_seen_at": "ALTER TABLE users ADD COLUMN last_seen_at DATETIME",
                "last_machine_id": "ALTER TABLE users ADD COLUMN last_machine_id VARCHAR(128)",
                "note": "ALTER TABLE users ADD COLUMN note TEXT",
            }
            for name, sql in alters.items():
                if name not in cols:
                    conn.execute(text(sql))


def log_event(
    db: Session,
    event_type: str,
    *,
    username: str | None = None,
    machine_id: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        AuthEvent(
            event_type=event_type,
            username=username,
            machine_id=(machine_id or None),
            detail=detail,
            created_at=datetime.utcnow(),
        )
    )


def touch_user(user: User, machine_id: str | None = None) -> None:
    user.last_seen_at = datetime.utcnow()
    if machine_id:
        user.last_machine_id = machine_id.strip() or user.last_machine_id


def append_license_history(user: User, code: str, days: int, plan: str) -> None:
    hist: list = []
    try:
        hist = json.loads(user.license_history or "[]")
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    hist.append(
        {
            "code": code,
            "days": days,
            "plan": plan,
            "at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    # 只保留最近 50 条
    user.license_history = json.dumps(hist[-50:], ensure_ascii=False)
    user.last_license_code = code


# ---------------- DTO ----------------
class Credentials(BaseModel):
    username: str
    password: str
    machine_id: str | None = None


class ViolationReport(BaseModel):
    username: str
    level: str  # red / yellow


class ActivateBody(BaseModel):
    code: str
    machine_id: str | None = None


class HeartbeatBody(BaseModel):
    machine_id: str | None = None


class GenerateLicensesBody(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    plan: str = "pro"
    days: int = Field(default=365, ge=1, le=3650)
    max_machines: int = Field(default=2, ge=1, le=20)
    note: str | None = None


class GrantBody(BaseModel):
    username: str
    plan: str = "pro"
    days: int = Field(default=365, ge=1, le=3650)
    max_machines: int | None = None


class UserNoteBody(BaseModel):
    username: str
    note: str = ""


class ChangePasswordBody(BaseModel):
    """用户自助改密：需旧密码。"""
    username: str
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class AdminResetPasswordBody(BaseModel):
    """管理员强制重置密码。Header: X-Admin-Secret"""
    username: str
    new_password: str = Field(min_length=6, max_length=128)


def user_view(u: User) -> dict:
    active = is_license_active(u)
    machines = _parse_machines(u.machines)
    hist: list = []
    try:
        hist = json.loads(u.license_history or "[]")
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    return {
        "username": u.username,
        "plan": u.plan,
        "role": u.role,
        "violation_count": u.violation_count,
        "banned": u.banned,
        "banned_reason": u.banned_reason,
        "expire_at": u.expire_at.isoformat() + "Z" if u.expire_at else None,
        "license_active": active,
        "license_required": LICENSE_REQUIRED,
        "license_reason": license_reason(u),
        "machines_bound": len(machines),
        "max_machines": u.max_machines or DEFAULT_MAX_MACHINES,
        "last_license_code": u.last_license_code,
        "license_history": hist,
        "last_seen_at": u.last_seen_at.isoformat() + "Z" if u.last_seen_at else None,
        "last_machine_id": u.last_machine_id,
        "note": u.note,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
    }


# ---------------- 应用 ----------------
app = FastAPI(title="西瓜短剧Agent · 认证服务", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_schema()


# 导入即迁移，避免 TestClient / 多 worker 漏跑 startup
init_db()


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "license_required": LICENSE_REQUIRED,
        "trial_days": TRIAL_DAYS,
        "security": {
            "jwt_ttl_seconds": JWT_TTL_SECONDS,
            "capability_ttl_seconds": CAP_TTL_SECONDS,
            "pbkdf2_iters": PBKDF2_ITERS,
            "token_alg": "HS256",
            "hardening": "industrial-v1",
        },
    }


@app.post("/auth/register")
def register(body: Credentials, db: Session = Depends(get_db)) -> dict:
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise HTTPException(409, "用户名已存在")
    expire = datetime.utcnow() + timedelta(days=TRIAL_DAYS) if TRIAL_DAYS > 0 else None
    user = User(
        username=body.username,
        password_hash=hash_pw(body.password),
        plan="trial" if TRIAL_DAYS > 0 else "free",
        expire_at=expire,
        max_machines=DEFAULT_MAX_MACHINES,
        machines="[]",
        license_history="[]",
    )
    if body.machine_id:
        bind_machine(user, body.machine_id)
        touch_user(user, body.machine_id)
    db.add(user)
    log_event(db, "register", username=user.username, machine_id=body.machine_id, detail="新用户注册")
    db.commit()
    db.refresh(user)
    cap = make_capability(user, body.machine_id)
    out = {"token": make_token(user.username, body.machine_id), "user": user_view(user)}
    if cap:
        out["capability"] = cap
        out["capability_ttl"] = CAP_TTL_SECONDS
    return out


@app.post("/auth/login")
def login(body: Credentials, db: Session = Depends(get_db)) -> dict:
    user = db.scalars(select(User).where(User.username == body.username)).first()
    if not user or not verify_pw(body.password, user.password_hash):
        log_event(db, "login_fail", username=body.username, machine_id=body.machine_id, detail="密码错误或用户不存在")
        db.commit()
        raise HTTPException(401, "用户名或密码错误")
    if user.banned:
        log_event(db, "login_banned", username=user.username, machine_id=body.machine_id, detail=user.banned_reason)
        db.commit()
        raise HTTPException(403, detail={"banned": True, "message": "账号已封禁", "reason": user.banned_reason})
    if body.machine_id:
        ensure_machine_allowed(user, body.machine_id, auto_bind=True)
    touch_user(user, body.machine_id)
    log_event(db, "login", username=user.username, machine_id=body.machine_id, detail=f"plan={user.plan}")
    db.commit()
    db.refresh(user)
    cap = make_capability(user, body.machine_id)
    out = {"token": make_token(user.username, body.machine_id), "user": user_view(user)}
    if cap:
        out["capability"] = cap
        out["capability_ttl"] = CAP_TTL_SECONDS
    return out


@app.post("/auth/change-password")
def change_password(body: ChangePasswordBody, db: Session = Depends(get_db)) -> dict:
    """用户自助改密（登录页「重置密码」）：校验旧密码后写入新哈希。与 8787 管理平台共用 auth.db。"""
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(400, "请输入用户名")
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if body.old_password == body.new_password:
        raise HTTPException(400, "新密码不能与旧密码相同")
    user = db.scalars(select(User).where(User.username == username)).first()
    if not user or not verify_pw(body.old_password, user.password_hash):
        log_event(db, "login_fail", username=username, detail="改密失败：旧密码错误")
        db.commit()
        raise HTTPException(401, "用户名或旧密码错误")
    if user.banned:
        raise HTTPException(403, detail={"banned": True, "message": "账号已封禁", "reason": user.banned_reason})
    user.password_hash = hash_pw(body.new_password)
    log_event(db, "change_password", username=user.username, detail="用户自行修改密码")
    db.commit()
    return {"ok": True, "message": "密码已修改，请使用新密码登录", "username": user.username}


@app.get("/auth/me")
def me(
    user: User = Depends(current_user),
    x_machine_id: str | None = Header(default=None, alias="X-Machine-Id"),
    db: Session = Depends(get_db),
) -> dict:
    if x_machine_id:
        try:
            ensure_machine_allowed(user, x_machine_id, auto_bind=True)
            touch_user(user, x_machine_id)
            db.commit()
            db.refresh(user)
        except HTTPException:
            view = user_view(user)
            view["license_active"] = False
            view["license_reason"] = "当前设备未授权或已达绑定上限"
            return view
    view = user_view(user)
    cap = make_capability(user, x_machine_id)
    if cap:
        view = dict(view)
        view["capability"] = cap
        view["capability_ttl"] = CAP_TTL_SECONDS
    return view


@app.post("/auth/heartbeat")
def heartbeat(body: HeartbeatBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """客户端周期心跳：校验授权 + 机器绑定。"""
    if body.machine_id:
        ensure_machine_allowed(user, body.machine_id, auto_bind=True)
    touch_user(user, body.machine_id)
    log_event(
        db,
        "heartbeat",
        username=user.username,
        machine_id=body.machine_id,
        detail=f"active={is_license_active(user)} plan={user.plan}",
    )
    db.commit()
    db.refresh(user)
    view = user_view(user)
    if not view["license_active"] and LICENSE_REQUIRED:
        raise HTTPException(
            403,
            detail={
                "license": True,
                "message": view["license_reason"] or "授权无效",
                "user": view,
            },
        )
    cap = make_capability(user, body.machine_id)
    if cap:
        view = dict(view)
        view["capability"] = cap
        view["capability_ttl"] = CAP_TTL_SECONDS
    return view


@app.post("/license/activate")
def activate_license(body: ActivateBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    code = body.code.strip().upper().replace(" ", "")
    if not code:
        raise HTTPException(400, "请输入卡密")
    key = db.scalars(select(LicenseKey).where(LicenseKey.code == code)).first()
    if not key:
        log_event(db, "activate_fail", username=user.username, machine_id=body.machine_id, detail=f"卡密不存在:digest={license_code_digest(code)}")
        db.commit()
        raise HTTPException(404, "卡密不存在")
    if key.used:
        log_event(
            db,
            "activate_fail",
            username=user.username,
            machine_id=body.machine_id,
            detail=f"卡密已使用:digest={license_code_digest(code)} by {key.used_by}",
        )
        db.commit()
        raise HTTPException(409, f"卡密已被 {key.used_by or '其他账号'} 使用")

    now = datetime.utcnow()
    base = user.expire_at if user.expire_at and user.expire_at > now else now
    user.expire_at = base + timedelta(days=key.days)
    user.plan = key.plan
    user.max_machines = key.max_machines
    if body.machine_id:
        bind_machine(user, body.machine_id)
    touch_user(user, body.machine_id)
    append_license_history(user, code, key.days, key.plan)

    key.used = True
    key.used_by = user.username
    key.used_at = now
    log_event(
        db,
        "activate",
        username=user.username,
        machine_id=body.machine_id,
        detail=f"code={code} +{key.days}d plan={key.plan}",
    )
    db.commit()
    db.refresh(user)
    cap = make_capability(user, body.machine_id)
    out = {"ok": True, "user": user_view(user), "added_days": key.days}
    if cap:
        out["capability"] = cap
        out["capability_ttl"] = CAP_TTL_SECONDS
    out["token"] = make_token(user.username, body.machine_id)
    return out


@app.post("/admin/licenses")
def admin_generate_licenses(
    body: GenerateLicensesBody,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """管理员发卡。Header: X-Admin-Secret"""
    codes: list[str] = []
    for _ in range(body.count):
        for _attempt in range(8):
            code = gen_license_code()
            if not db.scalars(select(LicenseKey).where(LicenseKey.code == code)).first():
                break
        else:
            raise HTTPException(500, "卡密生成冲突，请重试")
        db.add(
            LicenseKey(
                code=code,
                plan=body.plan,
                days=body.days,
                max_machines=body.max_machines,
                note=body.note,
            )
        )
        codes.append(code)
    db.commit()
    return {
        "count": len(codes),
        "plan": body.plan,
        "days": body.days,
        "max_machines": body.max_machines,
        "codes": codes,
    }


@app.post("/admin/grant")
def admin_grant(
    body: GrantBody,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """管理员直接给用户延期（不消耗卡密）。"""
    user = db.scalars(select(User).where(User.username == body.username)).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    now = datetime.utcnow()
    base = user.expire_at if user.expire_at and user.expire_at > now else now
    user.expire_at = base + timedelta(days=body.days)
    user.plan = body.plan
    if body.max_machines is not None:
        user.max_machines = body.max_machines
    db.commit()
    db.refresh(user)
    return {"ok": True, "user": user_view(user)}


@app.post("/admin/unbind-machines")
def admin_unbind(
    body: GrantBody,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """清空用户机器绑定（重装/换机）。"""
    user = db.scalars(select(User).where(User.username == body.username)).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.machines = "[]"
    db.commit()
    db.refresh(user)
    return {"ok": True, "user": user_view(user)}


@app.get("/admin/users")
def admin_list_users(
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """用户记录列表（管理员）。"""
    rows = db.scalars(select(User).order_by(User.id.desc())).all()
    return {"count": len(rows), "users": [user_view(u) for u in rows]}


@app.post("/admin/user-note")
def admin_set_user_note(
    body: UserNoteBody,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """设置用户备注（管理员）。"""
    user = db.scalars(select(User).where(User.username == body.username)).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.note = (body.note or "").strip() or None
    log_event(db, "admin_note", username=user.username, detail=f"备注={(user.note or '')[:80]}")
    db.commit()
    db.refresh(user)
    return {"ok": True, "user": user_view(user)}


@app.post("/admin/reset-password")
def admin_reset_password(
    body: AdminResetPasswordBody,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """管理员强制重置登录密码（无需旧密码）。与 8787 管理页「重置登录密码」同一库。"""
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(400, "请输入用户名")
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    user = db.scalars(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.password_hash = hash_pw(body.new_password)
    log_event(db, "admin_reset_password", username=user.username, detail="管理员重置登录密码")
    db.commit()
    db.refresh(user)
    return {"ok": True, "message": "密码已重置", "username": user.username}


@app.get("/admin/licenses")
def admin_list_licenses(
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """卡密记录列表（管理员）。"""
    rows = db.scalars(select(LicenseKey).order_by(LicenseKey.id.desc())).all()
    items = []
    for k in rows:
        items.append(
            {
                "id": k.id,
                "code": k.code,
                "plan": k.plan,
                "days": k.days,
                "max_machines": k.max_machines,
                "note": k.note,
                "used": k.used,
                "used_by": k.used_by,
                "used_at": k.used_at.isoformat() + "Z" if k.used_at else None,
                "created_at": k.created_at.isoformat() + "Z" if k.created_at else None,
            }
        )
    return {"count": len(items), "licenses": items}


@app.get("/admin/events")
def admin_list_events(
    limit: int = 100,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """联网监控流水：注册/登录/激活/心跳。"""
    limit = max(1, min(500, limit))
    rows = db.scalars(select(AuthEvent).order_by(AuthEvent.id.desc()).limit(limit)).all()
    items = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "username": e.username,
            "machine_id": e.machine_id,
            "detail": e.detail,
            "created_at": e.created_at.isoformat() + "Z" if e.created_at else None,
        }
        for e in rows
    ]
    return {"count": len(items), "events": items}


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
