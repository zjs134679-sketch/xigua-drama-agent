"""生产任务队列 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.compliance.enforce import ticket_username
from app.services.jobs import cancel_job, enqueue_job, get_job, job_view, list_jobs
from app.services.license_gate import require_valid_license

router = APIRouter(prefix="/jobs", tags=["jobs"])


class EnqueueBody(BaseModel):
    job_type: str
    payload: dict | None = None
    drama_id: int | None = None
    episode_id: int | None = None
    storyboard_id: int | None = None
    username: str | None = None
    message: str | None = None


@router.get("")
def api_list_jobs(
    status: str | None = None,
    drama_id: int | None = None,
    limit: int = Query(40, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    rows = list_jobs(db, status=status, drama_id=drama_id, limit=limit)
    return {"jobs": [job_view(j) for j in rows]}


@router.get("/{job_id}")
def api_get_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job_view(job)


@router.post("")
def api_enqueue(
    body: EnqueueBody,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
    # E1：任务属主取自票据身份，防止冒充他人提交任务/吃三振
    job = enqueue_job(
        db,
        job_type=body.job_type,
        payload=body.payload,
        drama_id=body.drama_id,
        episode_id=body.episode_id,
        storyboard_id=body.storyboard_id,
        username=ticket_username(_license) or body.username,
        message=body.message,
    )
    return job_view(job)


@router.post("/{job_id}/cancel")
def api_cancel(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = cancel_job(db, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job_view(job)
