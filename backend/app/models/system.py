"""系统表 —— 本产品新增：账号、违规记录、算力节点。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin


class User(Base, TimestampMixin):
    """本地缓存的用户态；权威账号/封号状态以 auth-server 为准。"""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(Text, default="free")  # free / paid（收费钩子预留）
    expire_at: Mapped[datetime | None] = mapped_column(DateTime)  # 到期（预留）
    role: Mapped[str] = mapped_column(Text, default="user")
    violation_count: Mapped[int] = mapped_column(Integer, default=0)  # 红线累计
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    banned_reason: Mapped[str | None] = mapped_column(Text)


class Violation(Base):
    """合规违规审计。每命中一条红线/确认黄线即留痕。"""

    __tablename__ = "violations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text, nullable=False)  # red / yellow
    word: Mapped[str | None] = mapped_column(Text)  # 命中词（脱敏存储可后续处理）
    category: Mapped[str | None] = mapped_column(Text)  # 命中词分类标签（占位/演示/...）
    source: Mapped[str | None] = mapped_column(Text)  # image_prompt / script / ...
    context: Mapped[str | None] = mapped_column(Text)  # 原文片段
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ComputeNode(Base, TimestampMixin):
    """算力节点：本地 ComfyUI / 国内远程主机 / 云 API。"""

    __tablename__ = "compute_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)  # local_comfy / remote_comfy / cloud_api
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str | None] = mapped_column(Text)  # 远程主机鉴权
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[str | None] = mapped_column(Text)  # JSON: image/video/...
    last_status: Mapped[str | None] = mapped_column(Text)  # online / offline
    extra: Mapped[str | None] = mapped_column(Text)  # JSON 杂项
