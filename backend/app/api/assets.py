from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import ArtStyle, Character, ImageGeneration, Prop, Scene, Storyboard
from app.schemas.assets import (
    ArtStyleCreate,
    ArtStyleUpdate,
    CharacterGenerateRequest,
    PropGenerateRequest,
    SceneGenerateRequest,
)
from app.services.asset_generation import (
    AssetGenerationError,
    ComplianceBlocked,
    GenerationOutcome,
    generate_character_asset,
    generate_prop_asset,
    generate_scene_asset,
)
from app.services.prompt_polish import batch_polish_prompts, polish_prompt

styles_router = APIRouter(prefix="/art-styles", tags=["art-styles"])
assets_router = APIRouter(prefix="/assets", tags=["assets"])


def style_view(row: ArtStyle) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "prompt_suffix": row.prompt_suffix,
        "lora": row.lora,
        "thumbnail": row.thumbnail,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@styles_router.get("")
def list_styles(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ArtStyle).order_by(ArtStyle.sort_order, ArtStyle.id)).all()
    return [style_view(row) for row in rows]


@styles_router.get("/{style_id}")
def get_style(style_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ArtStyle, style_id)
    if row is None:
        raise HTTPException(404, "画风不存在")
    return style_view(row)


@styles_router.post("", status_code=201)
def create_style(body: ArtStyleCreate, db: Session = Depends(get_db)) -> dict:
    row = ArtStyle(**body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return style_view(row)


@styles_router.put("/{style_id}")
def update_style(style_id: int, body: ArtStyleUpdate, db: Session = Depends(get_db)) -> dict:
    row = db.get(ArtStyle, style_id)
    if row is None:
        raise HTTPException(404, "画风不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return style_view(row)


@styles_router.delete("/{style_id}")
def delete_style(style_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ArtStyle, style_id)
    if row is None:
        raise HTTPException(404, "画风不存在")
    db.delete(row)
    db.commit()
    return {"deleted": True}


def _outcome_view(outcome: GenerationOutcome) -> dict:
    return {
        "status": outcome.result.status,
        "asset_id": outcome.asset.id,
        "image_url": outcome.result.image_url,
        "local_path": outcome.result.image_path,
        "prompt": outcome.prompt,
        "workflow": outcome.workflow,
        "warn": outcome.compliance.warn,
        "warn_hits": [hit.__dict__ for hit in outcome.compliance.hits] if outcome.compliance.warn else [],
        "meta": outcome.result.meta,
    }


def _error_response(exc: Exception):
    if isinstance(exc, ComplianceBlocked):
        return JSONResponse(
            status_code=451,
            content={
                "blocked": True,
                "level": "red",
                "hits": [hit.__dict__ for hit in exc.result.hits],
                "violation_count": exc.enforcement["violation_count"],
                "banned": exc.enforcement["banned"],
                "message": "内容触发红线，已拦截并记录",
            },
        )
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, AssetGenerationError):
        raise HTTPException(502, str(exc)) from exc
    raise exc


_HISTORY_TARGETS = {
    "character": (Character, "character_id"),
    "scene": (Scene, "scene_id"),
    "prop": (Prop, "prop_id"),
    "storyboard": (Storyboard, "storyboard_id"),
}


def _history_view(row: ImageGeneration) -> dict:
    return {
        "id": row.id,
        "image_url": row.image_url,
        "local_path": row.local_path,
        "prompt": row.prompt,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@assets_router.get("/history")
def list_asset_history(target_type: str, target_id: int, db: Session = Depends(get_db)) -> list[dict]:
    target_config = _HISTORY_TARGETS.get(target_type)
    if target_config is None:
        raise HTTPException(400, "不支持的素材类型")
    model, field = target_config
    target = db.get(model, target_id)
    if target is None or getattr(target, "deleted_at", None) is not None:
        raise HTTPException(404, "素材不存在")
    rows = db.scalars(
        select(ImageGeneration)
        .where(getattr(ImageGeneration, field) == target_id, ImageGeneration.status == "completed")
        .order_by(ImageGeneration.id.desc())
        .limit(3)
    ).all()
    return [_history_view(row) for row in rows]


@assets_router.post("/history/{image_gen_id}/use")
def use_asset_history(image_gen_id: int, db: Session = Depends(get_db)) -> dict:
    generation = db.get(ImageGeneration, image_gen_id)
    if generation is None or generation.status != "completed":
        raise HTTPException(404, "历史图片不存在")
    target_config = _HISTORY_TARGETS.get(generation.image_type or "")
    if target_config is None:
        raise HTTPException(400, "历史图片未关联素材")
    model, field = target_config
    target_id = getattr(generation, field)
    target = db.get(model, target_id) if target_id is not None else None
    if target is None or getattr(target, "deleted_at", None) is not None:
        raise HTTPException(404, "关联素材不存在")

    selected_image = generation.image_url or generation.local_path
    if isinstance(target, Storyboard):
        target.composed_image = selected_image
        target.status = "image_done"
    else:
        target.image_url = generation.image_url
        target.local_path = generation.local_path
    db.commit()
    return {
        "target_type": generation.image_type,
        "target_id": target_id,
        "image_url": selected_image,
        "local_path": generation.local_path,
    }


@assets_router.post("/character/generate")
async def generate_character(body: CharacterGenerateRequest, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    try:
        outcome = await generate_character_asset(
            db,
            character_id=body.character_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=body.username,
            scene_id=body.scene_id,
            action=body.action,
            node_id=body.node_id,
            resolution=body.resolution,
            extra=body.extra,
            view_type=body.view_type,
        )
        return _outcome_view(outcome)
    except (ComplianceBlocked, LookupError, AssetGenerationError) as exc:
        return _error_response(exc)


@assets_router.post("/scene/generate")
async def generate_scene(body: SceneGenerateRequest, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    try:
        outcome = await generate_scene_asset(
            db,
            scene_id=body.scene_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=body.username,
            node_id=body.node_id,
            resolution=body.resolution,
            extra=body.extra,
        )
        return _outcome_view(outcome)
    except (ComplianceBlocked, LookupError, AssetGenerationError) as exc:
        return _error_response(exc)


@assets_router.post("/prop/generate")
async def generate_prop(body: PropGenerateRequest, db: Session = Depends(get_db)):
    ensure_active_user(db, body.username)
    try:
        outcome = await generate_prop_asset(
            db,
            prop_id=body.prop_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=body.username,
            node_id=body.node_id,
            resolution=body.resolution,
            extra=body.extra,
        )
        return _outcome_view(outcome)
    except (ComplianceBlocked, LookupError, AssetGenerationError) as exc:
        return _error_response(exc)

# ── 提示词润色 ─────────────────────────────────────────────────────────

from pydantic import BaseModel as _BaseModel


class PolishRequest(_BaseModel):
    asset_type: str = "character"
    prompt: str
    context: str | None = None
    temperature: float = 0.5


class BatchPolishRequest(_BaseModel):
    asset_type: str = "storyboard"
    items: list[dict]
    temperature: float = 0.5


@assets_router.post("/polish-prompt")
def api_polish_prompt(body: PolishRequest, db: Session = Depends(get_db)):
    try:
        polished = polish_prompt(
            db,
            prompt=body.prompt,
            asset_type=body.asset_type,
            context=body.context,
            temperature=body.temperature,
        )
        return {"original": body.prompt, "polished": polished, "asset_type": body.asset_type}
    except Exception as exc:
        raise HTTPException(502, f"润色失败：{exc}")


@assets_router.post("/polish-prompt/batch")
def api_batch_polish(body: BatchPolishRequest, db: Session = Depends(get_db)):
    try:
        results = batch_polish_prompts(
            db,
            items=body.items,
            asset_type=body.asset_type,
            temperature=body.temperature,
        )
        return {"results": results}
    except Exception as exc:
        raise HTTPException(502, f"批量润色失败：{exc}")
