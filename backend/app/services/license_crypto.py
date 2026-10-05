"""授权能力票验签（与 auth-server 共享 XIGUA_AUTH_SECRET）。

能力票（typ=capability）为短期 JWT，本地后端可先验签再放行，
减少对 auth-server 的每次往返；无法替代服务端最终裁决。
"""
from __future__ import annotations

import os
import time
from typing import Any

import jwt

from app.core.config import settings


def _auth_secret() -> str:
    return (
        os.environ.get("XIGUA_AUTH_SECRET")
        or getattr(settings, "auth_secret", None)
        or ""
    ).strip()


def verify_capability(
    token: str | None,
    *,
    machine_id: str | None = None,
) -> dict[str, Any] | None:
    """验证能力票。失败返回 None。"""
    secret = _auth_secret()
    if not token or not secret or secret == "dev-secret-change-me-in-prod":
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "capability":
        return None
    if not payload.get("lic"):
        return None
    token_mid = (payload.get("mid") or "").strip()
    req_mid = (machine_id or "").strip()
    if token_mid and req_mid and token_mid != req_mid:
        return None
    # exp 已由 jwt 校验；再保险
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload
