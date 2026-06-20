from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import User
from app.services.compliance.enforce import sync_banned_state


def ensure_active_user(db: Session, username: str | None) -> None:
    username = username or "local"
    sync_banned_state(db, username)
    user = db.scalars(select(User).where(User.username == username)).first()
    if user and user.banned:
        raise HTTPException(
            403,
            detail={
                "banned": True,
                "message": "账号已封禁，无法继续使用",
                "reason": user.banned_reason,
            },
        )
