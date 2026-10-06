"""授权能力票验签（Ed25519 公钥验签；客户端不再持有任何签名密钥）。

能力票（typ=capability）为短期 JWT（EdDSA），由 auth-server 的 Ed25519 私钥签发；
本地后端用 XIGUA_CAPABILITY_PUBKEY（base64，32 字节原始公钥）验签后放行，
减少对 auth-server 的每次往返；无法替代服务端最终裁决。

注意：旧 HS256 能力票自本版本起作废（仅接受 EdDSA），
升级后客户端需重新登录/心跳以获取新能力票。
"""
from __future__ import annotations

import base64
import os
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.config import settings


def _capability_pubkey() -> Ed25519PublicKey | None:
    raw = (
        os.environ.get("XIGUA_CAPABILITY_PUBKEY")
        or getattr(settings, "capability_pubkey", None)
        or ""
    ).strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4))
        if len(data) != 32:
            return None
        return Ed25519PublicKey.from_public_bytes(data)
    except Exception:
        return None


def verify_capability(
    token: str | None,
    *,
    machine_id: str | None = None,
) -> dict[str, Any] | None:
    """验证能力票。失败返回 None（闭合失败：无公钥配置时直接拒绝）。"""
    pubkey = _capability_pubkey()
    if not token or pubkey is None:
        return None
    try:
        payload = jwt.decode(
            token,
            pubkey,
            algorithms=["EdDSA"],
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
