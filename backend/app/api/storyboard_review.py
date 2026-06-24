"""分镜审核 Agent API。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.services.llm.client import LLMNotConfigured
from app.services.storyboard_review import (
    StoryboardReviewError,
    latest_storyboard_review,
    list_storyboard_reviews,
    remediate_storyboard_review,
    run_storyboard_review,
)

router = APIRouter(prefix="/storyboard-review", tags=["storyboard-review"])


class ReviewRequest(BaseModel):
    episode_id: int
    username: str | None = None
    instruction: str | None = None


class RemediationRequest(BaseModel):
    username: str | None = None
    instruction: str | None = None


@router.post("")
def create_review(body: ReviewRequest, db: Session = Depends(get_db)) -> dict:
    ensure_active_user(db, body.username)
    try:
        return run_storyboard_review(db, body.episode_id, body.instruction)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (StoryboardReviewError, LLMNotConfigured) as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"审核 Agent 调用失败: {exc}") from exc


@router.get("/latest")
def get_latest_review(episode_id: int, db: Session = Depends(get_db)) -> dict | None:
    return latest_storyboard_review(db, episode_id)


@router.get("")
def get_review_history(episode_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return list_storyboard_reviews(db, episode_id)


@router.post("/{review_id}/remediate")
def remediate_review(review_id: int, body: RemediationRequest, db: Session = Depends(get_db)) -> dict:
    ensure_active_user(db, body.username)
    try:
        return remediate_storyboard_review(db, review_id, body.instruction)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (StoryboardReviewError, LLMNotConfigured) as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"整改 Agent 调用失败: {exc}") from exc
