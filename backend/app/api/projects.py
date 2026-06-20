from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import Character, Drama, Episode, Prop, Scene
from app.schemas.project import (
    AssetPromptUpdate,
    DramaCreate,
    EpisodeCreate,
    NovelImportRequest,
)
from app.services.asset_generation import build_character_prompt
from app.services.compliance import check, enforce
from app.services.novel_split import split_novel

router = APIRouter(prefix="/projects", tags=["projects"])


def drama_view(d: Drama) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "description": d.description,
        "genre": d.genre,
        "style": d.style,
        "status": d.status,
        "total_episodes": d.total_episodes,
    }


def episode_view(e: Episode, with_content: bool = False) -> dict:
    v = {
        "id": e.id,
        "drama_id": e.drama_id,
        "episode_number": e.episode_number,
        "title": e.title,
        "status": e.status,
        "has_content": bool(e.content),
        "has_script": bool(e.script_content),
    }
    if with_content:
        v["content"] = e.content
        v["script_content"] = e.script_content
    return v


@router.post("")
def create_drama(body: DramaCreate, db: Session = Depends(get_db)) -> dict:
    d = Drama(title=body.title, description=body.description, genre=body.genre, style=body.style)
    db.add(d)
    db.commit()
    return drama_view(d)


@router.get("")
def list_dramas(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Drama).where(Drama.deleted_at.is_(None)).order_by(Drama.id.desc())).all()
    return [drama_view(d) for d in rows]


@router.get("/{drama_id}")
def get_drama(drama_id: int, db: Session = Depends(get_db)) -> dict:
    d = db.get(Drama, drama_id)
    if not d or d.deleted_at is not None:
        raise HTTPException(404, "项目不存在")
    return drama_view(d)


@router.post("/{drama_id}/episodes")
def create_episode(drama_id: int, body: EpisodeCreate, db: Session = Depends(get_db)) -> dict:
    if not db.get(Drama, drama_id):
        raise HTTPException(404, "项目不存在")
    e = Episode(
        drama_id=drama_id,
        episode_number=body.episode_number,
        title=body.title,
        content=body.content,
    )
    db.add(e)
    db.commit()
    return episode_view(e, with_content=True)


@router.post("/{drama_id}/import-novel")
def import_novel(drama_id: int, body: NovelImportRequest, db: Session = Depends(get_db)) -> dict:
    """整篇小说 → 合规 → 自动分集（按章/按长度）→ 批量建分集。"""
    ensure_active_user(db, body.username)
    drama = db.get(Drama, drama_id)
    if not drama:
        raise HTTPException(404, "项目不存在")

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "请粘贴小说原文")

    result = check(text)
    if result.blocked:
        audit = enforce.record_violation(db, body.username, result, "novel_import")
        raise HTTPException(
            status_code=451,
            detail={
                "blocked": True,
                "level": "red",
                "message": "小说原文触发红线，已拦截并记录",
                "hits": [hit.__dict__ for hit in result.hits],
                "violation_count": audit["violation_count"],
                "banned": audit["banned"],
            },
        )

    chapters = split_novel(text, body.max_chars)
    if not chapters:
        raise HTTPException(400, "未能从原文中切分出内容")

    start = db.scalars(
        select(Episode.episode_number)
        .where(Episode.drama_id == drama_id)
        .order_by(Episode.episode_number.desc())
    ).first() or 0

    created: list[Episode] = []
    for offset, chapter in enumerate(chapters, start=1):
        episode = Episode(
            drama_id=drama_id,
            episode_number=start + offset,
            title=chapter["title"],
            content=chapter["content"],
        )
        db.add(episode)
        created.append(episode)
    db.commit()

    return {
        "drama_id": drama_id,
        "created": len(created),
        "warn": result.warn,
        "episodes": [episode_view(e) for e in created],
    }


@router.get("/{drama_id}/episodes")
def list_episodes(drama_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Episode)
        .where(Episode.drama_id == drama_id, Episode.deleted_at.is_(None))
        .order_by(Episode.episode_number)
    ).all()
    return [episode_view(e) for e in rows]


@router.get("/episodes/{episode_id}")
def get_episode(episode_id: int, db: Session = Depends(get_db)) -> dict:
    e = db.get(Episode, episode_id)
    if not e or e.deleted_at is not None:
        raise HTTPException(404, "分集不存在")
    return episode_view(e, with_content=True)


@router.get("/{drama_id}/characters")
def list_characters(drama_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Character).where(Character.drama_id == drama_id, Character.deleted_at.is_(None)).order_by(Character.id)
    ).all()
    return [
        {"id": c.id, "name": c.name, "role": c.role, "appearance": c.appearance,
         "personality": c.personality, "description": c.description, "image_url": c.image_url,
         # 可编辑出图提示词：已存优先，否则给一个可改的自动建议
         "image_prompt": c.image_prompt or build_character_prompt(c, None, None)}
        for c in rows
    ]


@router.patch("/characters/{character_id}")
def update_character_prompt(character_id: int, body: AssetPromptUpdate, db: Session = Depends(get_db)) -> dict:
    c = db.get(Character, character_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(404, "角色不存在")
    c.image_prompt = body.prompt
    db.commit()
    return {"id": c.id, "image_prompt": c.image_prompt}


@router.get("/{drama_id}/scenes")
def list_scenes(drama_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Scene).where(Scene.drama_id == drama_id, Scene.deleted_at.is_(None)).order_by(Scene.id)
    ).all()
    return [
        {"id": s.id, "location": s.location, "time": s.time, "prompt": s.prompt,
         "status": s.status, "image_url": s.image_url}
        for s in rows
    ]


@router.patch("/scenes/{scene_id}")
def update_scene_prompt(scene_id: int, body: AssetPromptUpdate, db: Session = Depends(get_db)) -> dict:
    s = db.get(Scene, scene_id)
    if s is None or s.deleted_at is not None:
        raise HTTPException(404, "场景不存在")
    s.prompt = body.prompt
    db.commit()
    return {"id": s.id, "prompt": s.prompt}


@router.get("/{drama_id}/props")
def list_props(drama_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Prop).where(Prop.drama_id == drama_id, Prop.deleted_at.is_(None)).order_by(Prop.id)
    ).all()
    return [
        {"id": p.id, "name": p.name, "type": p.type, "description": p.description, "prompt": p.prompt}
        for p in rows
    ]
