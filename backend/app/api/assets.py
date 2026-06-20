from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import ArtStyle
from app.schemas.assets import (
    ArtStyleCreate,
    ArtStyleUpdate,
    CharacterGenerateRequest,
    SceneGenerateRequest,
)
from app.services.asset_generation import (
    AssetGenerationError,
    ComplianceBlocked,
    GenerationOutcome,
    generate_character_asset,
    generate_scene_asset,
)

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
        )
        return _outcome_view(outcome)
    except (ComplianceBlocked, LookupError, AssetGenerationError) as exc:
        return _error_response(exc)
