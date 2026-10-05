# -*- coding: utf-8 -*-
"""一次性补丁：强化 auth-server 加密与授权票据。"""
from __future__ import annotations

import ast
from pathlib import Path

path = Path(r"E:\xigua Agent  密码管理\auth-server\app\main.py")
text = path.read_text(encoding="utf-8")

old = """LICENSE_REQUIRED = os.environ.get("XIGUA_LICENSE_REQUIRED", "1").strip() not in ("0", "false", "False")
LATEST_VERSION = os.environ.get("XIGUA_LATEST_VERSION", "0.1.0")
DOWNLOAD_URL = os.environ.get("XIGUA_DOWNLOAD_URL", "")
"""
new = """LICENSE_REQUIRED = os.environ.get("XIGUA_LICENSE_REQUIRED", "1").strip() not in ("0", "false", "False")
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
"""
if old not in text:
    raise SystemExit("config block not found")
text = text.replace(old, new, 1)

old = """def hash_pw(pw: str) -> str:
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
"""
new = """def hash_pw(pw: str) -> str:
    \"\"\"PBKDF2-HMAC-SHA256（默认 60 万次迭代）+ 16 字节盐。\"\"\"
    salt = os.urandom(16)
    iters = PBKDF2_ITERS
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters)
    return f"{salt.hex()}:{dk.hex()}:{iters}"


def verify_pw(pw: str, stored: str) -> bool:
    \"\"\"兼容旧格式 salt:dk（固定 20 万次）与新格式 salt:dk:iters。\"\"\"
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
    \"\"\"访问令牌：含 exp / jti / 可选机器绑定 mid。\"\"\"
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
    \"\"\"短期能力票：本地后端可离线验签（共享 SECRET）。\"\"\"
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
    \"\"\"卡密指纹（HMAC），用于日志脱敏。\"\"\"
    raw = code.strip().upper().encode("utf-8")
    return hmac.new(LICENSE_PEPPER.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:16]
"""
if old not in text:
    raise SystemExit("hash block not found")
text = text.replace(old, new, 1)

old = """def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
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
"""
new = """def current_user(
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
"""
if old not in text:
    raise SystemExit("current_user not found")
text = text.replace(old, new, 1)

# register + login returns
text = text.replace(
    'return {"token": make_token(user.username), "user": user_view(user)}',
    "cap = make_capability(user, body.machine_id)\n"
    '    out = {"token": make_token(user.username, body.machine_id), "user": user_view(user)}\n'
    "    if cap:\n"
    '        out["capability"] = cap\n'
    '        out["capability_ttl"] = CAP_TTL_SECONDS\n'
    "    return out",
)

old = '    return {"ok": True, "user": user_view(user), "added_days": key.days}\n'
new = """    cap = make_capability(user, body.machine_id)
    out = {"ok": True, "user": user_view(user), "added_days": key.days}
    if cap:
        out["capability"] = cap
        out["capability_ttl"] = CAP_TTL_SECONDS
    out["token"] = make_token(user.username, body.machine_id)
    return out
"""
if old not in text:
    raise SystemExit("activate return not found")
text = text.replace(old, new, 1)

old = """    return view


@app.post("/license/activate")
"""
new = """    cap = make_capability(user, body.machine_id)
    if cap:
        view = dict(view)
        view["capability"] = cap
        view["capability_ttl"] = CAP_TTL_SECONDS
    return view


@app.post("/license/activate")
"""
if old not in text:
    raise SystemExit("heartbeat return not found")
text = text.replace(old, new, 1)

old = """    return user_view(user)


@app.post("/auth/heartbeat")
"""
new = """    view = user_view(user)
    cap = make_capability(user, x_machine_id)
    if cap:
        view = dict(view)
        view["capability"] = cap
        view["capability_ttl"] = CAP_TTL_SECONDS
    return view


@app.post("/auth/heartbeat")
"""
if old not in text:
    raise SystemExit("me return not found")
text = text.replace(old, new, 1)

old = 'def health() -> dict:\n    return {"status": "ok", "license_required": LICENSE_REQUIRED, "trial_days": TRIAL_DAYS}\n'
new = """def health() -> dict:
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
"""
if old not in text:
    raise SystemExit("health not found")
text = text.replace(old, new, 1)

text = text.replace(
    'detail=f"卡密不存在:{code}"',
    'detail=f"卡密不存在:digest={license_code_digest(code)}"',
)
text = text.replace(
    'detail=f"卡密已被使用:{code} by {key.used_by}"',
    'detail=f"卡密已使用:digest={license_code_digest(code)} by {key.used_by}"',
)

# make_capability uses is_license_active before it's defined - need to move functions
# Currently make_capability is inserted before is_license_active - RUNTIME ERROR
# Fix: move make_capability after is_license_active or use forward ref only for type

path.write_text(text, encoding="utf-8")
ast.parse(text)
print("written + syntax ok")

# Check order of definitions
lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if line.startswith("def make_capability") or line.startswith("def is_license_active"):
        print(f"{i}: {line}")
