"""违规留痕 + 三振封号（本地版；auth-server 接入后以云端为权威）。

留痕的命中词做**掩码存储**，避免真实敏感词以明文落库。
"""
from __future__ import annotations

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
    return {"violation_count": user.violation_count, "banned": user.banned}
