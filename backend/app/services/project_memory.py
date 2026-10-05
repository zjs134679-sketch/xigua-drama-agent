"""项目级记忆与手册注入 —— 所有 Agent / 改编 / 出图可复用。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, Drama, Scene


def get_drama_manuals(db: Session, drama_id: int) -> dict[str, Any]:
    d = db.get(Drama, drama_id)
    if d is None:
        return {}
    memory: dict = {}
    if d.memory_json:
        try:
            memory = json.loads(d.memory_json)
        except json.JSONDecodeError:
            memory = {}
    model_map: dict = {}
    if d.model_map_json:
        try:
            model_map = json.loads(d.model_map_json)
        except json.JSONDecodeError:
            model_map = {}
    return {
        "director_manual": d.director_manual or "",
        "visual_manual": d.visual_manual or "",
        "banned_elements": d.banned_elements or "",
        "memory": memory,
        "model_map": model_map,
    }


def update_drama_manuals(
    db: Session,
    drama_id: int,
    *,
    director_manual: str | None = None,
    visual_manual: str | None = None,
    banned_elements: str | None = None,
    memory: dict | None = None,
    model_map: dict | None = None,
) -> dict[str, Any]:
    d = db.get(Drama, drama_id)
    if d is None or d.deleted_at is not None:
        raise LookupError("项目不存在")
    if director_manual is not None:
        d.director_manual = director_manual
    if visual_manual is not None:
        d.visual_manual = visual_manual
    if banned_elements is not None:
        d.banned_elements = banned_elements
    if memory is not None:
        d.memory_json = json.dumps(memory, ensure_ascii=False)
    if model_map is not None:
        d.model_map_json = json.dumps(model_map, ensure_ascii=False)
    db.commit()
    return get_drama_manuals(db, drama_id)


def refresh_character_memory(db: Session, drama_id: int) -> dict:
    """把当前角色定妆描述写入 memory，供后续 Agent 锁定外貌。"""
    chars = db.scalars(
        select(Character).where(Character.drama_id == drama_id, Character.deleted_at.is_(None))
    ).all()
    scenes = db.scalars(
        select(Scene).where(Scene.drama_id == drama_id, Scene.deleted_at.is_(None))
    ).all()
    memory = {
        "characters": [
            {
                "id": c.id,
                "name": c.name,
                "appearance": c.appearance or c.image_prompt,
                "image_url": c.image_url,
                "role": c.role,
            }
            for c in chars
        ],
        "scenes": [
            {"id": s.id, "location": s.location, "prompt": s.prompt, "image_url": s.image_url}
            for s in scenes
        ],
    }
    d = db.get(Drama, drama_id)
    if d is None:
        raise LookupError("项目不存在")
    d.memory_json = json.dumps(memory, ensure_ascii=False)
    db.commit()
    return memory


def build_project_context_block(db: Session, drama_id: int) -> str:
    manuals = get_drama_manuals(db, drama_id)
    parts: list[str] = ["【项目约束·必须遵守】"]
    if manuals.get("director_manual"):
        parts.append(f"导演手册：\n{manuals['director_manual'][:1500]}")
    if manuals.get("visual_manual"):
        parts.append(f"视觉手册：\n{manuals['visual_manual'][:1500]}")
    if manuals.get("banned_elements"):
        parts.append(f"禁用元素：{manuals['banned_elements'][:500]}")
    mem = manuals.get("memory") or {}
    chars = mem.get("characters") or []
    if chars:
        lines = [
            f"- {c.get('name')}: {(c.get('appearance') or '')[:120]}"
            for c in chars[:20]
        ]
        parts.append("角色外貌锁定：\n" + "\n".join(lines))
    return "\n\n".join(parts) if len(parts) > 1 else ""


DEFAULT_MODEL_MAP = {
    "llm_profile": "default",
    "image_node_id": None,
    "video_test_quality": "test",
    "video_final_quality": "final",
    "video_duration": 5,
    "default_resolution_test": "704x480",
    "default_resolution_final": "1024x576",
    "notes": "final=LTX2.3 定稿/口型（Wan 已移除）",
}


def resolve_model_map(db: Session, drama_id: int | None) -> dict:
    if drama_id is None:
        return dict(DEFAULT_MODEL_MAP)
    manuals = get_drama_manuals(db, drama_id)
    merged = dict(DEFAULT_MODEL_MAP)
    merged.update(manuals.get("model_map") or {})
    return merged
