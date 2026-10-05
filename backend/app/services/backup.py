"""项目数据备份 / 恢复（JSON，不含大体量二进制；媒体仍在 data/oss）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.domain import (
    Character,
    Drama,
    Episode,
    NovelChapter,
    NovelEvent,
    Prop,
    Scene,
    Storyboard,
)


def export_project(db: Session, drama_id: int) -> dict[str, Any]:
    d = db.get(Drama, drama_id)
    if d is None or d.deleted_at is not None:
        raise LookupError("项目不存在")

    episodes = list(
        db.scalars(select(Episode).where(Episode.drama_id == drama_id, Episode.deleted_at.is_(None))).all()
    )
    ep_ids = [e.id for e in episodes]
    storyboards = []
    if ep_ids:
        storyboards = list(
            db.scalars(
                select(Storyboard).where(Storyboard.episode_id.in_(ep_ids), Storyboard.deleted_at.is_(None))
            ).all()
        )
    chars = list(db.scalars(select(Character).where(Character.drama_id == drama_id, Character.deleted_at.is_(None))).all())
    scenes = list(db.scalars(select(Scene).where(Scene.drama_id == drama_id, Scene.deleted_at.is_(None))).all())
    props = list(db.scalars(select(Prop).where(Prop.drama_id == drama_id, Prop.deleted_at.is_(None))).all())
    events = list(db.scalars(select(NovelEvent).where(NovelEvent.drama_id == drama_id, NovelEvent.deleted_at.is_(None))).all())
    chapters = list(
        db.scalars(select(NovelChapter).where(NovelChapter.drama_id == drama_id, NovelChapter.deleted_at.is_(None))).all()
    )

    payload = {
        "format": "xigua-project-v1",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "drama": {
            "title": d.title,
            "description": d.description,
            "genre": d.genre,
            "style": d.style,
            "director_manual": d.director_manual,
            "visual_manual": d.visual_manual,
            "banned_elements": d.banned_elements,
            "memory_json": d.memory_json,
            "model_map_json": d.model_map_json,
        },
        "episodes": [
            {
                "episode_number": e.episode_number,
                "title": e.title,
                "content": e.content,
                "script_content": e.script_content,
                "status": e.status,
            }
            for e in episodes
        ],
        "characters": [
            {
                "name": c.name,
                "role": c.role,
                "description": c.description,
                "appearance": c.appearance,
                "personality": c.personality,
                "image_prompt": c.image_prompt,
                "image_url": c.image_url,
            }
            for c in chars
        ],
        "scenes": [
            {
                "location": s.location,
                "time": s.time,
                "prompt": s.prompt,
                "image_url": s.image_url,
            }
            for s in scenes
        ],
        "props": [{"name": p.name, "type": p.type, "description": p.description, "prompt": p.prompt} for p in props],
        "storyboards": [
            {
                "episode_number": next((e.episode_number for e in episodes if e.id == sb.episode_id), None),
                "storyboard_number": sb.storyboard_number,
                "title": sb.title,
                "dialogue": sb.dialogue,
                "image_prompt": sb.image_prompt,
                "video_prompt": sb.video_prompt,
                "duration": sb.duration,
                "video_url": sb.video_url,
                "composed_image": sb.composed_image,
            }
            for sb in storyboards
        ],
        "novel_chapters": [
            {"chapter_number": c.chapter_number, "title": c.title, "content": c.content, "summary": c.summary}
            for c in chapters
        ],
        "novel_events": [
            {
                "event_number": e.event_number,
                "title": e.title,
                "summary": e.summary,
                "characters": e.characters,
                "location": e.location,
                "conflict": e.conflict,
                "emotion": e.emotion,
                "key_dialogue": e.key_dialogue,
                "raw_excerpt": e.raw_excerpt,
                "status": e.status,
            }
            for e in events
        ],
    }

    out_dir = settings.data_dir / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"project_{drama_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    path = out_dir / fname
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "filename": fname, "download_hint": f"/oss/../backups/{fname}", "bytes": path.stat().st_size, "payload_preview": {"title": d.title, "episodes": len(episodes), "events": len(events)}}
