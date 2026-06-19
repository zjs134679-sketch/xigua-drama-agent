from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import Drama, Episode
from app.schemas.project import DramaCreate, EpisodeCreate

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
