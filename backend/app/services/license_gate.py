"""本地后端授权门禁：能力票验签 + 回源 auth-server。

开发默认关闭（XIGUA_LICENSE_ENFORCE=false）；
正式包务必开启，防止绕过前端直接调生成 API。
"""
from __future__ import annotations

import httpx
from fastapi import Header, HTTPException

from app.core.config import settings
from app.services.license_crypto import verify_capability


async def require_valid_license(
    authorization: str | None = Header(default=None),
    x_machine_id: str | None = Header(default=None, alias="X-Machine-Id"),
    x_capability: str | None = Header(default=None, alias="X-Capability"),
) -> dict:
    if not settings.license_enforce:
        return {"skipped": True, "license_active": True}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail="需要登录授权后才能使用此功能")

    # 1) 优先验本地能力票（工业加固：短时效 + 机器绑定 + Ed25519 公钥验签）
    cap = verify_capability(x_capability, machine_id=x_machine_id)
    if cap is not None:
        return {
            "license_active": True,
            "username": cap.get("sub"),
            "plan": cap.get("plan"),
            "via": "capability",
        }

    # 2) 回源 auth-server 权威校验
    headers = {"Authorization": authorization}
    if x_machine_id:
        headers["X-Machine-Id"] = x_machine_id

    url = settings.auth_server_url.rstrip("/") + "/auth/me"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, detail=f"授权服务不可达：{exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(401, detail="登录已失效，请重新登录")
    if resp.status_code >= 400:
        raise HTTPException(403, detail="授权校验失败")

    data = resp.json() if resp.content else {}
    if data.get("banned"):
        raise HTTPException(
            403,
            detail={"banned": True, "message": "账号已封禁", "reason": data.get("banned_reason")},
        )
    if data.get("license_required", True) and not data.get("license_active", False):
        raise HTTPException(
            403,
            detail={
                "license": True,
                "message": data.get("license_reason") or "授权无效或已过期，请激活卡密",
            },
        )
    # 若回源成功且带了新能力票字段，调用方可通过 /auth/me 刷新；此处仅放行
    data["via"] = "auth_server"
    return data
