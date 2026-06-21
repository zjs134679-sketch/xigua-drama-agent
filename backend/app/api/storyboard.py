"""分镜接口：剧本 → 分镜拆解（LLM）→ 逐镜出图（合规 + 算力节点）。"""
from __future__ import annotations

import json
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import Episode, Storyboard
from app.schemas.storyboard import (
    BatchDeleteRequest,
    BatchGenerateImageRequest,
    BatchGeneratePromptRequest,
    StoryboardGenerateRequest,
    StoryboardImageRequest,
    StoryboardPromptUpdate,
)
from app.services.agents.storyboard_agent import break_storyboards, save_storyboards
from app.services.asset_generation import (
    AssetGenerationError,
    ComplianceBlocked,
    GenerationOutcome,
    generate_storyboard_image,
)
from app.services.compliance import FilterResult, check, enforce
from app.services.llm.client import LLMNotConfigured

router = APIRouter(prefix="/storyboard", tags=["storyboard"])


def storyboard_view(sb: Storyboard) -> dict:
    return {
        "id": sb.id,
        "episode_id": sb.episode_id,
        "storyboard_number": sb.storyboard_number,
        "title": sb.title,
        "location": sb.location,
        "time": sb.time,
        "shot_type": sb.shot_type,
        "angle": sb.angle,
        "movement": sb.movement,
        "action": sb.action,
        "dialogue": sb.dialogue,
        "atmosphere": sb.atmosphere,
        "image_prompt": sb.image_prompt,
        "duration": sb.duration,
        "image_url": sb.composed_image,
        "status": sb.status,
    }


def _hits(*results: FilterResult) -> list[dict]:
    unique: dict[tuple[str, str, str], dict] = {}
    for result in results:
        for hit in result.hits:
            unique[(hit.word, hit.level, hit.category)] = hit.__dict__
    return list(unique.values())


def _block(db: Session, username: str | None, result: FilterResult, source: str, message: str) -> None:
    audit = enforce.record_violation(db, username, result, source)
    raise HTTPException(
        status_code=451,
        detail={
            "blocked": True,
            "level": "red",
            "message": message,
            "hits": _hits(result),
            "violation_count": audit["violation_count"],
            "banned": audit["banned"],
        },
    )


@router.get("")
def list_storyboards(episode_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    return [storyboard_view(sb) for sb in rows]


@router.post("/generate")
def generate_storyboards(req: StoryboardGenerateRequest, db: Session = Depends(get_db)) -> dict:
    ensure_active_user(db, req.username)
    ep = db.get(Episode, req.episode_id)
    if not ep:
        raise HTTPException(404, "分集不存在")
    script = ep.script_content
    if not script:
        raise HTTPException(400, "该分集还没有剧本，请先在剧本页生成剧本")

    input_result = check(script)
    if input_result.blocked:
        _block(db, req.username, input_result, "storyboard_input", "剧本触发红线，已拦截并记录")

    try:
        shots = break_storyboards(db, script, req.temperature)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")
    except (ValueError, KeyError) as e:
        raise HTTPException(502, f"分镜结果解析失败: {e}")

    output_result = check(json.dumps(shots, ensure_ascii=False))
    if output_result.blocked:
        _block(db, req.username, output_result, "storyboard_output", "分镜结果触发红线，已拦截并记录")

    count = save_storyboards(db, ep.id, shots)
    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == ep.id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    return {
        "episode_id": ep.id,
        "count": count,
        "storyboards": [storyboard_view(sb) for sb in rows],
        "warn": input_result.warn or output_result.warn,
        "hits": _hits(input_result, output_result),
    }


def _outcome_view(outcome: GenerationOutcome) -> dict:
    return {
        "status": outcome.result.status,
        "image_url": outcome.result.image_url,
        "local_path": outcome.result.image_path,
        "prompt": outcome.prompt,
        "warn": outcome.compliance.warn,
        "warn_hits": [hit.__dict__ for hit in outcome.compliance.hits] if outcome.compliance.warn else [],
    }


@router.post("/{storyboard_id}/image")
async def storyboard_image(storyboard_id: int, body: StoryboardImageRequest, db: Session = Depends(get_db)):
    """单镜出图（前端「出图」按钮 + 前端批量循环都走这个）。"""
    ensure_active_user(db, body.username)
    try:
        outcome = await generate_storyboard_image(
            db,
            storyboard_id=storyboard_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=body.username,
            node_id=body.node_id,
            resolution=body.resolution,
            extra=body.extra,
        )
        return _outcome_view(outcome)
    except ComplianceBlocked as exc:
        return JSONResponse(
            status_code=451,
            content={
                "blocked": True,
                "level": "red",
                "hits": [hit.__dict__ for hit in exc.result.hits],
                "violation_count": exc.enforcement["violation_count"],
                "banned": exc.enforcement["banned"],
                "message": "画面提示词触发红线，已拦截并记录",
            },
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AssetGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.patch("/{storyboard_id}")
def update_storyboard_prompt(storyboard_id: int, body: StoryboardPromptUpdate, db: Session = Depends(get_db)) -> dict:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise HTTPException(404, "分镜不存在")
    sb.image_prompt = body.image_prompt
    db.commit()
    db.refresh(sb)
    return storyboard_view(sb)


@router.post("/batch-delete")
def batch_delete_storyboards(body: BatchDeleteRequest, db: Session = Depends(get_db)) -> dict:
    if not body.ids:
        raise HTTPException(400, "ids 不能为空")
    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.id.in_(body.ids), Storyboard.deleted_at.is_(None))
    ).all()
    now = datetime.utcnow()
    count = 0
    for sb in rows:
        sb.deleted_at = now
        count += 1
    db.commit()
    return {"deleted": count}


@router.post("/batch-generate-images")
async def batch_generate_storyboard_images(body: BatchGenerateImageRequest, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    if not body.ids:
        raise HTTPException(400, "ids 不能为空")
    results: list[dict] = []
    for sb_id in body.ids:
        try:
            outcome = await generate_storyboard_image(
                db,
                storyboard_id=sb_id,
                full_prompt=None,
                art_style_id=body.art_style_id,
                username=body.username,
                node_id=body.node_id,
                resolution=body.resolution,
            )
            results.append({"storyboard_id": sb_id, "status": "completed", "image_url": outcome.result.image_url})
        except ComplianceBlocked as exc:
            results.append({
                "storyboard_id": sb_id,
                "status": "blocked",
                "hits": [hit.__dict__ for hit in exc.result.hits],
            })
        except (LookupError, AssetGenerationError) as exc:
            results.append({"storyboard_id": sb_id, "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/batch-generate-prompts")
def batch_generate_storyboard_prompts(body: BatchGeneratePromptRequest, db: Session = Depends(get_db)) -> dict:
    """用 AI 为已选分镜润色/重新生成画面提示词。"""
    ensure_active_user(db, body.username)
    if not body.ids:
        raise HTTPException(400, "ids 不能为空")
    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.id.in_(body.ids), Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    if not rows:
        raise HTTPException(404, "没有找到有效的分镜")

    # 聚合分镜信息发给 LLM 一次生成所有提示词
    shots = []
    for sb in rows:
        shots.append({
            "number": sb.storyboard_number,
            "title": sb.title or "",
            "location": sb.location or "",
            "time": sb.time or "",
            "shot_type": sb.shot_type or "",
            "angle": sb.angle or "",
            "movement": sb.movement or "",
            "action": sb.action or "",
            "dialogue": sb.dialogue or "",
            "atmosphere": sb.atmosphere or "",
        })

    from app.services.agents.storyboard_agent import polish_prompts
    try:
        updated = polish_prompts(db, shots, body.temperature)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")

    # 回写
    by_number = {sb.storyboard_number: sb for sb in rows}
    count = 0
    for item in updated:
        num = item["number"]
        if num in by_number:
            by_number[num].image_prompt = item["prompt"]
            count += 1
    db.commit()
    return {"updated": count, "prompts": updated}
