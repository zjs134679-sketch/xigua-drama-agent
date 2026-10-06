"""异步任务队列：单 worker + 全局 Comfy 锁（8G 显卡同时只跑 1 个重任务）。"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.logging import logger
from app.models.domain import ProductionJob
from app.services.comfy_errors import classify_comfy_error

# 进程内：Comfy 重任务互斥（出图/出视频）
_comfy_lock = asyncio.Lock()
_worker_task: asyncio.Task | None = None
_stop = asyncio.Event()
_wake = asyncio.Event()
_loop: asyncio.AbstractEventLoop | None = None


def job_view(job: ProductionJob) -> dict[str, Any]:
    result = None
    payload = None
    try:
        result = json.loads(job.result_json) if job.result_json else None
    except json.JSONDecodeError:
        result = {"raw": job.result_json}
    try:
        payload = json.loads(job.payload_json) if job.payload_json else None
    except json.JSONDecodeError:
        payload = {"raw": job.payload_json}
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress or 0,
        "message": job.message,
        "error_msg": job.error_msg,
        "error_code": job.error_code,
        "drama_id": job.drama_id,
        "episode_id": job.episode_id,
        "storyboard_id": job.storyboard_id,
        "payload": payload,
        "result": result,
        "cancel_requested": bool(job.cancel_requested),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    payload: dict | None = None,
    drama_id: int | None = None,
    episode_id: int | None = None,
    storyboard_id: int | None = None,
    username: str | None = None,
    message: str | None = None,
) -> ProductionJob:
    job = ProductionJob(
        job_type=job_type,
        status="pending",
        progress=0,
        message=message or "排队中",
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        drama_id=drama_id,
        episode_id=episode_id,
        storyboard_id=storyboard_id,
        username=username,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _signal_wake()
    return job


def get_job(db: Session, job_id: int) -> ProductionJob | None:
    return db.get(ProductionJob, job_id)


def list_jobs(
    db: Session,
    *,
    status: str | None = None,
    drama_id: int | None = None,
    limit: int = 50,
) -> list[ProductionJob]:
    q = select(ProductionJob).order_by(ProductionJob.id.desc()).limit(min(limit, 200))
    if status:
        q = q.where(ProductionJob.status == status)
    if drama_id is not None:
        q = q.where(ProductionJob.drama_id == drama_id)
    return list(db.scalars(q).all())


def cancel_job(db: Session, job_id: int) -> ProductionJob | None:
    job = db.get(ProductionJob, job_id)
    if job is None:
        return None
    if job.status in ("completed", "failed", "cancelled"):
        return job
    job.cancel_requested = True
    if job.status == "pending":
        job.status = "cancelled"
        job.message = "已取消（未开始）"
        job.completed_at = datetime.utcnow()
        job.progress = 0
    else:
        job.message = "取消请求已提交，等待当前步骤结束…"
    db.commit()
    db.refresh(job)
    return job


def _signal_wake() -> None:
    global _loop
    if _loop is None:
        return
    try:
        _loop.call_soon_threadsafe(_wake.set)
    except RuntimeError:
        pass


def start_worker() -> None:
    """在 FastAPI lifespan 内启动后台 worker。"""
    global _worker_task, _loop, _stop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _stop = asyncio.Event()
    if _worker_task is None or _worker_task.done():
        _worker_task = _loop.create_task(_worker_loop(), name="xigua-job-worker")
        logger.info("生产任务 worker 已启动")


async def stop_worker() -> None:
    global _worker_task
    _stop.set()
    _wake.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _worker_task.cancel()
        _worker_task = None


async def _worker_loop() -> None:
    while not _stop.is_set():
        job_id = _claim_next_job()
        if job_id is None:
            _wake.clear()
            try:
                await asyncio.wait_for(_wake.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            continue
        await _run_job(job_id)


def _claim_next_job() -> int | None:
    with SessionLocal() as db:
        job = db.scalars(
            select(ProductionJob)
            .where(ProductionJob.status == "pending")
            .order_by(ProductionJob.id.asc())
            .limit(1)
        ).first()
        if job is None:
            return None
        if job.cancel_requested:
            job.status = "cancelled"
            job.message = "已取消"
            job.completed_at = datetime.utcnow()
            db.commit()
            return None
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.progress = 5
        job.message = "执行中…"
        db.commit()
        return job.id


async def _run_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(ProductionJob, job_id)
        if job is None:
            return
        if job.cancel_requested:
            job.status = "cancelled"
            job.message = "已取消"
            job.completed_at = datetime.utcnow()
            db.commit()
            return
        payload: dict = {}
        try:
            payload = json.loads(job.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}

        # C2: 按"是否真实调用 Comfy/云重任务"划分名单。
        # - export_timeline 是纯 ffmpeg 合成，不走 Comfy，不加锁；
        # - easy_pipeline 内部会出视频，必须加锁；
        # - image_generate 暂无 handler 实现（入队即 failed），不加锁，实现后再加。
        needs_comfy = job.job_type in (
            "video_generate",
            "video_batch",
            "easy_pipeline",
        )
        try:
            if needs_comfy:
                async with _comfy_lock:
                    await _dispatch(db, job, payload)
            else:
                await _dispatch(db, job, payload)
        except Exception as exc:  # noqa: BLE001
            code, msg = classify_comfy_error(str(exc))
            job.status = "failed"
            job.error_code = code
            job.error_msg = msg
            job.message = msg
            job.progress = 100
            job.completed_at = datetime.utcnow()
            db.commit()
            logger.warning("任务 %s 失败: %s", job_id, msg)
        finally:
            # C4: BaseException（如 worker 停止时的 CancelledError）不会被 except 捕获，
            # 这里兜底收尾，避免任务永久卡在 running
            try:
                db.refresh(job)
            except Exception:  # noqa: BLE001
                pass
            else:
                if job.status == "running":
                    job.status = "failed"
                    job.error_msg = "任务被中断（worker 停止或服务重启）"
                    job.message = job.error_msg
                    job.progress = 100
                    job.completed_at = datetime.utcnow()
                    try:
                        db.commit()
                    except Exception:  # noqa: BLE001
                        db.rollback()


async def _dispatch(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.services.jobs.handlers import handle_job

    await handle_job(db, job, payload)
    db.refresh(job)
    if job.status == "running":
        # handler 未主动收尾
        if job.cancel_requested:
            job.status = "cancelled"
            job.message = "已取消"
        else:
            job.status = "completed"
            job.progress = 100
            job.message = job.message or "完成"
        job.completed_at = datetime.utcnow()
        db.commit()


def update_progress(db: Session, job: ProductionJob, progress: int, message: str | None = None) -> None:
    job.progress = max(0, min(100, int(progress)))
    if message:
        job.message = message
    db.commit()


def finish_job(
    db: Session,
    job: ProductionJob,
    *,
    status: str = "completed",
    result: dict | None = None,
    error: str | None = None,
    message: str | None = None,
) -> None:
    job.status = status
    job.progress = 100
    job.completed_at = datetime.utcnow()
    if result is not None:
        job.result_json = json.dumps(result, ensure_ascii=False)
    if error:
        code, msg = classify_comfy_error(error)
        job.error_code = code
        job.error_msg = msg
        job.message = message or msg
    else:
        job.message = message or ("完成" if status == "completed" else job.message)
    db.commit()
