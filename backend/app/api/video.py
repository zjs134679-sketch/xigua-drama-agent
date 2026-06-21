"""视频生成 API：分镜图 → AI 视频片段（ComfyUI / 云端 API）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.services.video_generation import (
    ComplianceBlocked,
    VideoGenError,
    delete_video,
    generate_video_prompt,
    list_storyboard_videos,
    poll_video_status,
    submit_batch_video_generation,
    submit_video_generation,
)

router = APIRouter(prefix="/video", tags=["video"])


class VideoGenerateRequest:
    """由 pydantic 或 manual 解析。"""
    pass


from pydantic import BaseModel


class VideoGenBody(BaseModel):
    storyboard_id: int
    prompt: str | None = None
    model: str = "default"
    reference_mode: str = "single"
    node_id: int | None = None
    username: str | None = None
    duration: int = 4
    resolution: str = "1024x576"
    extra: str | None = None


class BatchVideoGenBody(BaseModel):
    storyboard_ids: list[int]
    model: str = "default"
    reference_mode: str = "single"
    node_id: int | None = None
    username: str | None = None
    duration: int = 4
    resolution: str = "1024x576"


class VideoPromptBody(BaseModel):
    storyboard_id: int
    model: str = "kling"
    mode: str = "multi_ref"


@router.post("/generate-prompt")
def gen_video_prompt(body: VideoPromptBody, db: Session = Depends(get_db)) -> dict:
    """根据分镜信息和目标模型，生成对应格式的视频提示词。"""
    from app.models.domain import Storyboard
    sb = db.get(Storyboard, body.storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise HTTPException(404, "分镜不存在")
    import asyncio
    prompt = asyncio.run(generate_video_prompt(
        sb,
        model=body.model,
        mode=body.mode,
    ))
    return {"storyboard_id": body.storyboard_id, "model": body.model, "mode": body.mode, "prompt": prompt}


@router.post("/generate")
async def generate_video(body: VideoGenBody, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    try:
        gen = await submit_video_generation(
            db,
            storyboard_id=body.storyboard_id,
            prompt=body.prompt,
            model=body.model,
            reference_mode=body.reference_mode,
            node_id=body.node_id,
            username=body.username,
            duration=body.duration,
            resolution=body.resolution,
            extra=body.extra,
        )
        return {
            "video_id": gen.id,
            "storyboard_id": gen.storyboard_id,
            "status": gen.status,
            "video_url": gen.video_url,
            "prompt": gen.prompt,
        }
    except ComplianceBlocked as exc:
        return JSONResponse(
            status_code=451,
            content={
                "blocked": True,
                "level": "red",
                "hits": [hit.__dict__ for hit in exc.result.hits],
                "violation_count": exc.enforcement["violation_count"],
                "banned": exc.enforcement["banned"],
                "message": "视频提示词触发红线",
            },
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except VideoGenError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/batch-generate")
async def batch_generate_video(body: BatchVideoGenBody, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    results = await submit_batch_video_generation(
        db,
        storyboard_ids=body.storyboard_ids,
        model=body.model,
        reference_mode=body.reference_mode,
        node_id=body.node_id,
        username=body.username,
        duration=body.duration,
        resolution=body.resolution,
    )
    return {"results": results}


@router.get("/poll/{video_id}")
def check_video_status(video_id: int, db: Session = Depends(get_db)) -> dict:
    return poll_video_status(db, video_id)


@router.post("/poll-batch")
def batch_check_video_status(body: dict, db: Session = Depends(get_db)) -> dict:
    from app.services.video_generation import batch_poll_video_status
    ids = body.get("ids", [])
    return {"results": batch_poll_video_status(db, ids)}


@router.get("/list/{storyboard_id}")
def list_videos(storyboard_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return list_storyboard_videos(db, storyboard_id)


@router.delete("/{video_id}")
def delete_video_endpoint(video_id: int, db: Session = Depends(get_db)) -> dict:
    return delete_video(db, video_id)
