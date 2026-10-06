"""小说事件图谱 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.jobs import enqueue_job, job_view
from app.services.novel_events import adapt_events_to_episode, extract_events_from_text, list_chapters, list_events

router = APIRouter(prefix="/events", tags=["events"])


class ExtractBody(BaseModel):
    drama_id: int
    text: str
    chapter_title: str | None = None
    chapter_number: int = 1
    async_mode: bool = False
    username: str | None = None


class AdaptBody(BaseModel):
    drama_id: int
    event_ids: list[int]
    episode_id: int | None = None
    episode_title: str | None = None
    async_mode: bool = False
    username: str | None = None


@router.get("/{drama_id}")
def api_list(drama_id: int, db: Session = Depends(get_db)) -> dict:
    return {
        "chapters": list_chapters(db, drama_id),
        "events": list_events(db, drama_id),
    }


@router.post("/extract")
async def api_extract(body: ExtractBody, db: Session = Depends(get_db)):
    if not (body.text or "").strip():
        raise HTTPException(400, "文本为空")
    if body.async_mode:
        job = enqueue_job(
            db,
            job_type="extract_novel_events",
            payload={
                "drama_id": body.drama_id,
                "text": body.text,
                "chapter_title": body.chapter_title,
                "chapter_number": body.chapter_number,
            },
            drama_id=body.drama_id,
            username=body.username,
            message="提取小说事件",
        )
        return {"async": True, "job": job_view(job)}
    try:
        result = await extract_events_from_text(
            db,
            drama_id=body.drama_id,
            text=body.text,
            chapter_title=body.chapter_title,
            chapter_number=body.chapter_number,
        )
        return {"async": False, **result}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        # LLM 调用失败（超时/429/5xx 等）不再静默吞掉，转 502
        raise HTTPException(502, str(exc)) from exc


@router.post("/adapt")
async def api_adapt(body: AdaptBody, db: Session = Depends(get_db)):
    if body.async_mode:
        job = enqueue_job(
            db,
            job_type="adapt_events",
            payload={
                "drama_id": body.drama_id,
                "event_ids": body.event_ids,
                "episode_id": body.episode_id,
                "episode_title": body.episode_title,
            },
            drama_id=body.drama_id,
            episode_id=body.episode_id,
            username=body.username,
            message="按事件改编剧本",
        )
        return {"async": True, "job": job_view(job)}
    try:
        result = await adapt_events_to_episode(
            db,
            drama_id=body.drama_id,
            event_ids=body.event_ids,
            episode_id=body.episode_id,
            episode_title=body.episode_title,
        )
        return {"async": False, **result}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
