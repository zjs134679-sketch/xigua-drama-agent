"""视频生成 API：分镜图 → AI 视频片段（ComfyUI / 云端 API）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.services.license_gate import require_valid_license
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
    # 与 ComfyUI LTX 上限对齐：默认 5 秒，后端仍会再钳一次
    duration: int = 5
    # 不传则按 quality_mode 默认：test=704x480 / final=1024x576
    resolution: str | None = None
    extra: str | None = None
    # 连续运镜：优先用上一镜视频尾帧作为本镜 i2v 起始帧
    use_prev_last_frame: bool = True
    # final=LTX-2.3 定稿（仅保留此档；传 test 也会回落 final）
    quality_mode: str = "final"
    # 异步入队（推荐批量/长任务）；false 则同步等待
    async_mode: bool = False


class BatchVideoGenBody(BaseModel):
    storyboard_ids: list[int]
    model: str = "default"
    reference_mode: str = "single"
    node_id: int | None = None
    username: str | None = None
    duration: int = 5
    resolution: str | None = None
    use_prev_last_frame: bool = True
    quality_mode: str = "final"
    async_mode: bool = True


class SelectVideoBody(BaseModel):
    storyboard_id: int
    video_id: int


class TrimBody(BaseModel):
    storyboard_id: int
    trim_in: float | None = None
    trim_out: float | None = None


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
async def generate_video(
    body: VideoGenBody,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
):
    ensure_active_user(db, body.username)
    if body.async_mode:
        from app.models.domain import Storyboard
        from app.services.jobs import enqueue_job, job_view

        sb = db.get(Storyboard, body.storyboard_id)
        job = enqueue_job(
            db,
            job_type="video_generate",
            payload={
                "storyboard_id": body.storyboard_id,
                "prompt": body.prompt,
                "model": body.model,
                "reference_mode": body.reference_mode,
                "node_id": body.node_id,
                "username": body.username,
                "duration": body.duration,
                "resolution": body.resolution,
                "extra": body.extra,
                "use_prev_last_frame": body.use_prev_last_frame,
                "quality_mode": body.quality_mode,
            },
            drama_id=None,
            episode_id=sb.episode_id if sb else None,
            storyboard_id=body.storyboard_id,
            username=body.username,
            message=f"出视频 {body.quality_mode}",
        )
        return {"async": True, "job": job_view(job), "status": "pending"}
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
            use_prev_last_frame=body.use_prev_last_frame,
            quality_mode=body.quality_mode,
        )
        payload = {
            "async": False,
            "video_id": gen.id,
            "storyboard_id": gen.storyboard_id,
            "status": gen.status,
            "video_url": gen.video_url,
            "prompt": gen.prompt,
            "error_msg": gen.error_msg,
            "quality_mode": getattr(gen, "quality_mode", body.quality_mode),
            "model": gen.model,
            "duration": gen.duration,
            "resolution": gen.resolution,
        }
        warning = getattr(gen, "audio_warning", None)
        if warning:
            payload["warning"] = warning
            payload["tts_duration"] = getattr(gen, "tts_duration", None)
        if getattr(gen, "kept_embedded_audio", False):
            payload["kept_embedded_audio"] = True
            payload["note"] = "音频驱动模式已保留视频内嵌配音（未做静音剥离）"
        return payload
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
        from app.services.comfy_errors import classify_comfy_error

        code, msg = classify_comfy_error(str(exc))
        raise HTTPException(502, detail={"error_code": code, "message": msg, "raw": str(exc)}) from exc


@router.post("/batch-generate")
async def batch_generate_video(body: BatchVideoGenBody, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    if body.async_mode:
        from app.services.jobs import enqueue_job, job_view

        job = enqueue_job(
            db,
            job_type="video_batch",
            payload={
                "storyboard_ids": body.storyboard_ids,
                "model": body.model,
                "reference_mode": body.reference_mode,
                "node_id": body.node_id,
                "username": body.username,
                "duration": body.duration,
                "resolution": body.resolution,
                "use_prev_last_frame": body.use_prev_last_frame,
                "quality_mode": body.quality_mode,
            },
            username=body.username,
            message=f"批量出视频 {body.quality_mode} ×{len(body.storyboard_ids)}",
        )
        return {"async": True, "job": job_view(job), "quality_mode": body.quality_mode}
    results = await submit_batch_video_generation(
        db,
        storyboard_ids=body.storyboard_ids,
        model=body.model,
        reference_mode=body.reference_mode,
        node_id=body.node_id,
        username=body.username,
        duration=body.duration,
        resolution=body.resolution,
        use_prev_last_frame=body.use_prev_last_frame,
        quality_mode=body.quality_mode,
    )
    return {"async": False, "results": results, "quality_mode": body.quality_mode}


@router.post("/select")
def select_video_version(body: SelectVideoBody, db: Session = Depends(get_db)) -> dict:
    """多版视频择优：把指定 VideoGeneration 设为分镜当前片。"""
    from app.models.domain import Storyboard, VideoGeneration

    sb = db.get(Storyboard, body.storyboard_id)
    gen = db.get(VideoGeneration, body.video_id)
    if sb is None or sb.deleted_at is not None:
        raise HTTPException(404, "分镜不存在")
    if gen is None or gen.deleted_at is not None or gen.storyboard_id != body.storyboard_id:
        raise HTTPException(404, "视频版本不存在")
    if not gen.video_url:
        raise HTTPException(400, "该版本没有可用视频地址")
    sb.video_url = gen.video_url
    sb.selected_video_id = gen.id
    if gen.last_frame_url:
        sb.last_frame_image = gen.last_frame_url
    if gen.duration:
        sb.duration = gen.duration
    db.commit()
    return {
        "storyboard_id": sb.id,
        "video_id": gen.id,
        "video_url": sb.video_url,
        "model": gen.model,
        "quality_mode": (gen.reference_mode or "").split("|")[0] if gen.reference_mode else None,
    }


@router.post("/trim")
def set_video_trim(body: TrimBody, db: Session = Depends(get_db)) -> dict:
    """设置本镜入点/出点（秒），导出时优先使用。"""
    from app.models.domain import Storyboard

    sb = db.get(Storyboard, body.storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise HTTPException(404, "分镜不存在")
    if body.trim_in is not None:
        sb.trim_in = max(0.0, float(body.trim_in))
    if body.trim_out is not None:
        sb.trim_out = max(0.0, float(body.trim_out))
    if sb.trim_in is not None and sb.trim_out is not None and sb.trim_out <= sb.trim_in:
        raise HTTPException(400, "trim_out 必须大于 trim_in")
    db.commit()
    return {"storyboard_id": sb.id, "trim_in": sb.trim_in, "trim_out": sb.trim_out}


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
