"""违规留痕 + 三振封号（本地版；auth-server 接入后以云端为权威）。

留痕的命中词做**掩码存储**，避免真实敏感词以明文落库。
"""
from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import User, Violation
from app.services.compliance.filter import FilterResult


def mask_word(word: str | None) -> str:
    if not word:
        return ""
    if len(word) <= 1:
        return "*"
    return word[0] + "*" * (len(word) - 1)


def _get_or_create_user(db: Session, username: str) -> User:
    user = db.scalars(select(User).where(User.username == username)).first()
    if user is None:
        user = User(username=username)
        db.add(user)
        db.flush()
    return user


def _remote_state(data: Any) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("banned"), bool):
        return None
    try:
        violation_count = max(0, int(data.get("violation_count", 0)))
    except (TypeError, ValueError):
        return None
    reason = data.get("banned_reason")
    return {
        "violation_count": violation_count,
        "banned": data["banned"],
        "banned_reason": reason if isinstance(reason, str) else None,
    }


def report_to_auth(username: str | None, result: FilterResult) -> dict | None:
    """Best-effort red-line report; local enforcement remains the offline fallback."""
    if result.level != "red":
        return None
    try:
        response = httpx.post(
            f"{settings.auth_server_url.rstrip('/')}/compliance/violation",
            json={"username": username or "local", "level": "red"},
            timeout=0.75,
        )
        response.raise_for_status()
        return _remote_state(response.json())
    except Exception:
        return None


def sync_banned_state(db: Session, username: str | None) -> dict | None:
    """Refresh one cached user from the auth server without breaking offline use."""
    username = username or "local"
    try:
        response = httpx.get(
            f"{settings.auth_server_url.rstrip('/')}/compliance/status",
            params={"username": username},
            timeout=0.75,
        )
        response.raise_for_status()
        state = _remote_state(response.json())
        if state is None:
            return None
        user = _get_or_create_user(db, username)
        user.violation_count = state["violation_count"]
        user.banned = state["banned"]
        user.banned_reason = state["banned_reason"]
        db.commit()
        return state
    except (httpx.HTTPError, TypeError, ValueError):
        return None
    except Exception:
        db.rollback()
        return None


def record_violation(db: Session, username: str | None, result: FilterResult, source: str = "image_prompt") -> dict:
    username = username or "local"
    user = _get_or_create_user(db, username)
    for h in result.hits:
        db.add(
            Violation(
                user_id=user.id,
                username=username,
                level=h.level,
                word=mask_word(h.word),  # 掩码：不存明文敏感词
                category=h.category,
                source=source,
            )
        )
    if result.level == "red":
        user.violation_count = (user.violation_count or 0) + 1
        if user.violation_count >= settings.ban_threshold:
            user.banned = True
            user.banned_reason = f"红线违规累计达到 {settings.ban_threshold} 次"
    db.commit()
    if result.level == "red":
        remote = report_to_auth(username, result)
        if remote is not None:
            try:
                user.violation_count = remote["violation_count"]
                user.banned = remote["banned"]
                user.banned_reason = remote["banned_reason"]
                db.commit()
            except Exception:
                db.rollback()
    return {"violation_count": user.violation_count, "banned": user.banned}
