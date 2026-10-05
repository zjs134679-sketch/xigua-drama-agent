"""项目手册 / 记忆 / 模型地图 / 备份 / 生产监督。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.backup import export_project
from app.services.jobs import enqueue_job, job_view
from app.services.production_supervise import supervise_episode
from app.services.project_memory import (
    get_drama_manuals,
    refresh_character_memory,
    resolve_model_map,
    update_drama_manuals,
)

router = APIRouter(tags=["project-manuals"])


class ManualsBody(BaseModel):
    director_manual: str | None = None
    visual_manual: str | None = None
    banned_elements: str | None = None
    memory: dict | None = None
    model_map: dict | None = None


@router.get("/projects/{drama_id}/manuals")
def get_manuals(drama_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        data = get_drama_manuals(db, drama_id)
        data["model_map_resolved"] = resolve_model_map(db, drama_id)
        return data
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, str(exc)) from exc


@router.put("/projects/{drama_id}/manuals")
def put_manuals(drama_id: int, body: ManualsBody, db: Session = Depends(get_db)) -> dict:
    try:
        return update_drama_manuals(
            db,
            drama_id,
            director_manual=body.director_manual,
            visual_manual=body.visual_manual,
            banned_elements=body.banned_elements,
            memory=body.memory,
            model_map=body.model_map,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{drama_id}/memory/refresh")
def refresh_memory(drama_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return {"memory": refresh_character_memory(db, drama_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{drama_id}/backup")
def backup_project(drama_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return export_project(db, drama_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/production/supervise")
async def production_supervise(body: dict, db: Session = Depends(get_db)) -> dict:
    episode_id = body.get("episode_id")
    if not episode_id:
        raise HTTPException(400, "需要 episode_id")
    async_mode = bool(body.get("async_mode"))
    if async_mode:
        job = enqueue_job(
            db,
            job_type="production_supervise",
            payload={"episode_id": int(episode_id)},
            episode_id=int(episode_id),
            message="生产监督",
        )
        return {"async": True, "job": job_view(job)}
    try:
        report = await supervise_episode(db, episode_id=int(episode_id))
        return {"async": False, **report}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
