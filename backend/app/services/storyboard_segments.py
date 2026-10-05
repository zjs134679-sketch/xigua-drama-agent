"""运镜段落分组：把同一场景/连续动作的多镜标成同一 segment，便于 UI 与合镜。"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Storyboard
from app.services.storyboard_references import _same_continuity_scene


def list_episode_storyboards(db: Session, episode_id: int) -> list[Storyboard]:
    return list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number, Storyboard.id)
        ).all()
    )


def _auto_title(group: list[Storyboard]) -> str:
    first = group[0]
    movement = (first.movement or "").strip()
    location = (first.location or "").strip()
    if movement and location:
        return f"{movement}·{location}"
    if movement:
        return f"运镜：{movement}"
    if location:
        return f"连续场景：{location}"
    titles = [((s.title or "").strip()) for s in group if (s.title or "").strip()]
    if titles:
        return f"段落：{titles[0]}"
    return f"运镜段落（{len(group)} 镜）"


def assign_segment_fields(group: list[Storyboard], *, key: str | None = None, title: str | None = None) -> str:
    """给一组连续镜写入 segment_* 字段，返回 segment_key。"""
    if not group:
        return ""
    segment_key = key or group[0].segment_key or f"seg-{uuid.uuid4().hex[:10]}"
    segment_title = title or group[0].segment_title or _auto_title(group)
    total = len(group)
    for index, sb in enumerate(group, start=1):
        sb.segment_key = segment_key
        sb.segment_title = segment_title
        sb.segment_part = index
        sb.segment_total = total
    return segment_key


def refresh_episode_segments(db: Session, episode_id: int, *, commit: bool = True) -> list[dict]:
    """按场景连续性重算运镜段落。

    - 已有相同 segment_key 的相邻镜优先合并保留标题
    - 同场景连续且无打断标记的相邻镜自动成组
    - 单镜也写 segment_part=1/1，便于 UI 统一
    """
    rows = list_episode_storyboards(db, episode_id)
    if not rows:
        return []

    groups: list[list[Storyboard]] = []
    current: list[Storyboard] = [rows[0]]
    for prev, nxt in zip(rows, rows[1:]):
        same_key = bool(prev.segment_key and prev.segment_key == nxt.segment_key)
        continuous = _same_continuity_scene(prev, nxt)
        if same_key or continuous:
            current.append(nxt)
        else:
            groups.append(current)
            current = [nxt]
    groups.append(current)

    summaries: list[dict] = []
    for group in groups:
        # 保留组内已有 key/title（例如长镜拆解写入的）
        keys = [s.segment_key for s in group if s.segment_key]
        titles = [s.segment_title for s in group if s.segment_title]
        key = keys[0] if keys else None
        title = titles[0] if titles else None
        if len(group) == 1 and not key:
            # 孤立单镜：清空分组字段，避免误显示「段落」
            sb = group[0]
            sb.segment_key = None
            sb.segment_title = None
            sb.segment_part = None
            sb.segment_total = None
            continue
        segment_key = assign_segment_fields(group, key=key, title=title)
        summaries.append(
            {
                "segment_key": segment_key,
                "segment_title": group[0].segment_title,
                "storyboard_ids": [s.id for s in group],
                "storyboard_numbers": [s.storyboard_number for s in group],
                "count": len(group),
            }
        )
    if commit:
        db.commit()
    return summaries


def segment_view(sb: Storyboard) -> dict:
    if not sb.segment_key or not sb.segment_total or sb.segment_total < 2:
        return {
            "segment_key": None,
            "segment_title": None,
            "segment_part": None,
            "segment_total": None,
            "in_segment": False,
        }
    return {
        "segment_key": sb.segment_key,
        "segment_title": sb.segment_title,
        "segment_part": sb.segment_part,
        "segment_total": sb.segment_total,
        "in_segment": True,
    }


def episode_segment_summaries(db: Session, episode_id: int) -> list[dict]:
    rows = list_episode_storyboards(db, episode_id)
    buckets: dict[str, list[Storyboard]] = defaultdict(list)
    order: list[str] = []
    for sb in rows:
        if not sb.segment_key or not sb.segment_total or sb.segment_total < 2:
            continue
        if sb.segment_key not in buckets:
            order.append(sb.segment_key)
        buckets[sb.segment_key].append(sb)
    result = []
    for key in order:
        group = sorted(buckets[key], key=lambda s: (s.segment_part or 0, s.storyboard_number))
        result.append(
            {
                "segment_key": key,
                "segment_title": group[0].segment_title or _auto_title(group),
                "storyboard_ids": [s.id for s in group],
                "storyboard_numbers": [s.storyboard_number for s in group],
                "count": len(group),
            }
        )
    return result
