from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.services.compliance.enforce import ticket_username
from app.services.license_gate import require_valid_license
from app.models.domain import ArtStyle, Character, ImageGeneration, Prop, Scene, Storyboard
from app.schemas.assets import (
    ArtStyleCreate,
    ArtStyleUpdate,
    CharacterGenerateRequest,
    PropGenerateRequest,
    SceneGenerateRequest,
)
from app.services.art_style_pack import (
    art_prompt_template,
    extract_video_style_tags,
    load_pack,
    skill_key_for_style,
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


def style_view(row: ArtStyle, *, with_pack: bool = False) -> dict:
    """列表默认轻量返回；详情/pack 再带技能包元数据。"""
    data = {
        "id": row.id,
        "name": row.name,
        "prompt_suffix": row.prompt_suffix,
        "lora": row.lora,
        "thumbnail": row.thumbnail,
        "sort_order": row.sort_order,
        "constraint_manual": row.constraint_manual,
        "skill_key": None,
        "pack_files": [],
        "pack_complete": False,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if with_pack:
        skill_key = skill_key_for_style(row)
        pack = load_pack(skill_key) if skill_key else None
        data["skill_key"] = skill_key
        data["pack_files"] = pack["file_list"] if pack else []
        data["pack_complete"] = bool(pack and len(pack.get("file_list", [])) >= 10)
    return data


@styles_router.get("")
def list_styles(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ArtStyle).order_by(ArtStyle.sort_order, ArtStyle.id)).all()
    # 列表不做包扫描，避免卡顿导致前端像“没风格”
    return [style_view(row, with_pack=False) for row in rows]


@styles_router.post("/seed")
def seed_styles(
    force: bool = Query(False, description="true 时用技能包覆盖库内后缀与约束手册"),
    db: Session = Depends(get_db),
) -> dict:
    """从 data/skills/art_styles 同步西瓜原创画风预设。

    默认：空字段补全 + 明显短于完整手册的系统种子自动升级。
    force=1：强制用技能包覆盖。
    """
    from app.services.art_style_seed import seed_preset_art_styles

    result = seed_preset_art_styles(db, force=force)
    rows = db.scalars(select(ArtStyle).order_by(ArtStyle.sort_order, ArtStyle.id)).all()
    return {**result, "styles": [style_view(row) for row in rows]}


@styles_router.get("/{style_id}")
def get_style(style_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ArtStyle, style_id)
    if row is None:
        raise HTTPException(404, "画风不存在")
    return style_view(row, with_pack=True)


@styles_router.get("/{style_id}/pack")
def get_style_pack(style_id: int, db: Session = Depends(get_db)) -> dict:
    """返回画风技能包全文：constraint + prompts + direction。"""
    row = db.get(ArtStyle, style_id)
    if row is None:
        raise HTTPException(404, "画风不存在")
    key = skill_key_for_style(row)
    if not key:
        raise HTTPException(404, "该画风未绑定技能包")
    pack = load_pack(key)
    if pack is None:
        raise HTTPException(404, f"技能包不存在: {key}")
    return {
        "style_id": style_id,
        "style_name": row.name,
        **pack,
        "video_tags_en": extract_video_style_tags(key, prefer="en"),
        "video_tags_zh": extract_video_style_tags(key, prefer="zh"),
        "templates": {
            name: art_prompt_template(key, name)
            for name in ("character", "scene", "prop", "video")
        },
    }


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
    # 与分镜接口一致：返回 /oss/ 永久地址，避免 Comfy 临时 /view 链多图串台
    saved = None
    if outcome.asset is not None and outcome.asset.url:
        saved = outcome.asset.url
    elif outcome.result.image_path:
        from pathlib import Path
        saved = "/oss/" + Path(outcome.result.image_path).name
    return {
        "status": outcome.result.status,
        "asset_id": outcome.asset.id,
        "image_url": saved or outcome.result.image_url,
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


@assets_router.post("/upload")
async def upload_media(
    file: UploadFile = File(...),
    target_type: str = Form(...),
    target_id: int = Form(...),
    username: str | None = Form(None),
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
    """上传本地图片/视频并绑定到角色、场景、道具或分镜。

    target_type:
      - character / scene / prop → 图片
      - storyboard_image → 分镜图
      - storyboard_video → 分镜视频（成片台可用）
    """
    ensure_active_user(db, username)
    from app.services.media_upload import MediaUploadError, attach_media

    raw = await file.read()
    try:
        return attach_media(
            db,
            target_type=target_type,
            target_id=int(target_id),
            data=raw,
            original_name=file.filename or "upload.bin",
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except MediaUploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"上传失败：{exc}") from exc


@assets_router.post("/history/{image_gen_id}/use")
def use_asset_history(image_gen_id: int, db: Session = Depends(get_db)) -> dict:
    generation = db.get(ImageGeneration, image_gen_id)
    if generation is None or generation.status != "completed":
        raise HTTPException(404, "历史图片不存在")
    # image_type 缺失时按外键字段推断（拆镜/旧数据更稳）
    image_type = generation.image_type or ""
    if image_type not in _HISTORY_TARGETS:
        if generation.storyboard_id is not None:
            image_type = "storyboard"
        elif generation.character_id is not None:
            image_type = "character"
        elif generation.scene_id is not None:
            image_type = "scene"
        elif generation.prop_id is not None:
            image_type = "prop"
    target_config = _HISTORY_TARGETS.get(image_type)
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
        if not target.first_frame_image:
            target.first_frame_image = selected_image
        target.status = "image_done"
    else:
        target.image_url = generation.image_url or selected_image
        target.local_path = generation.local_path
    db.commit()
    return {
        "target_type": image_type,
        "target_id": target_id,
        "image_url": selected_image,
        "local_path": generation.local_path,
    }


@assets_router.post("/character/generate")
async def generate_character(
    body: CharacterGenerateRequest,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
):
    # E1：身份取自票据，不再信任请求体自填的 username
    username = ticket_username(_license) or body.username
    ensure_active_user(db, username)
    try:
        outcome = await generate_character_asset(
            db,
            character_id=body.character_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=username,
            scene_id=body.scene_id,
            action=body.action,
            node_id=body.node_id,
            resolution=body.resolution,
            steps=body.steps,
            extra=body.extra,
            view_type=body.view_type,
        )
        return _outcome_view(outcome)
    except (ComplianceBlocked, LookupError, AssetGenerationError) as exc:
        return _error_response(exc)


@assets_router.post("/scene/generate")
async def generate_scene(
    body: SceneGenerateRequest,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
):
    # E1：身份取自票据，不再信任请求体自填的 username
    username = ticket_username(_license) or body.username
    ensure_active_user(db, username)
    try:
        outcome = await generate_scene_asset(
            db,
            scene_id=body.scene_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=username,
            node_id=body.node_id,
            resolution=body.resolution,
            steps=body.steps,
            extra=body.extra,
        )
        return _outcome_view(outcome)
    except (ComplianceBlocked, LookupError, AssetGenerationError) as exc:
        return _error_response(exc)


@assets_router.post("/prop/generate")
async def generate_prop(
    body: PropGenerateRequest,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
):
    # E1：身份取自票据，不再信任请求体自填的 username
    username = ticket_username(_license) or body.username
    ensure_active_user(db, username)
    try:
        outcome = await generate_prop_asset(
            db,
            prop_id=body.prop_id,
            full_prompt=body.prompt,
            art_style_id=body.art_style_id,
            username=username,
            node_id=body.node_id,
            resolution=body.resolution,
            steps=body.steps,
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
