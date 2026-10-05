"""分镜接口：剧本 → 分镜拆解（LLM）→ 逐镜出图（合规 + 算力节点）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import Character, Episode, Storyboard, StoryboardCharacter
from app.services.license_gate import require_valid_license
from app.schemas.storyboard import (
    BatchDeleteRequest,
    BatchGenerateImageRequest,
    BatchGeneratePromptRequest,
    StoryboardGenerateRequest,
    StoryboardImageRequest,
    StoryboardSplitRequest,
    StoryboardUpdate,
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
from app.services.storyboard_references import resolve_storyboard_image_references
from app.services.storyboard_segments import (
    episode_segment_summaries,
    refresh_episode_segments,
    segment_view,
)
from app.services.storyboard_split import StoryboardSplitError, split_storyboard

router = APIRouter(prefix="/storyboard", tags=["storyboard"])

# 与 ComfyUI 单镜图生视频上限一致
STORYBOARD_MAX_DURATION = 5
STORYBOARD_DEFAULT_DURATION = 5


def _clamp_storyboard_duration(value: int | None) -> int:
    try:
        raw = int(value) if value is not None else STORYBOARD_DEFAULT_DURATION
    except (TypeError, ValueError):
        raw = STORYBOARD_DEFAULT_DURATION
    if raw <= 0:
        return STORYBOARD_DEFAULT_DURATION
    return max(1, min(raw, STORYBOARD_MAX_DURATION))


def storyboard_view(sb: Storyboard, db: Session) -> dict:
    reference_rows, reference_mode = resolve_storyboard_image_references(db, sb)
    seg = segment_view(sb)
    cast_links = db.scalars(
        select(StoryboardCharacter).where(StoryboardCharacter.storyboard_id == sb.id)
    ).all()
    missing_cast_images: list[str] = []
    for link in cast_links:
        ch = db.get(Character, link.character_id)
        if ch is None or ch.deleted_at is not None:
            continue
        if not (ch.image_url or ch.local_path):
            missing_cast_images.append(ch.name or f"id={ch.id}")
    has_scene_ref = any(r.kind == "scene" for r in reference_rows)
    has_char_ref = any(r.kind == "character" for r in reference_rows)
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
        "result": sb.result,
        "dialogue": sb.dialogue,
        "atmosphere": sb.atmosphere,
        "image_prompt": sb.image_prompt,
        "video_prompt": sb.video_prompt,
        "bgm_prompt": sb.bgm_prompt,
        "sound_effect": sb.sound_effect,
        "description": sb.description,
        # 历史数据可能仍是 10–15s，接口层统一钳到 5s，分镜工作台不再显示超限时长
        "duration": _clamp_storyboard_duration(sb.duration),
        "speaking_character_id": sb.speaking_character_id,
        "image_url": sb.composed_image,
        "first_frame_image": sb.first_frame_image,
        "last_frame_image": sb.last_frame_image,
        "video_url": sb.video_url or sb.composed_video_url,
        "status": sb.status,
        "scene_id": sb.scene_id,
        "segment_key": seg["segment_key"],
        "segment_title": seg["segment_title"],
        "segment_part": seg["segment_part"],
        "segment_total": seg["segment_total"],
        "in_segment": seg["in_segment"],
        "reference_mode": reference_mode,
        "reference_images": [
            {
                "url": row.url,
                "preview_url": row.url if row.url.startswith(("/", "http://", "https://")) else "/oss/" + Path(row.url).name,
                "kind": row.kind,
                "label": row.label,
                "source_storyboard_id": row.source_storyboard_id,
                "asset_name": row.asset_name,
                "character_id": row.character_id,
                "prop_id": row.prop_id,
                "is_speaker": row.is_speaker,
            }
            for row in reference_rows
        ],
        "readiness": {
            "ref_count": len(reference_rows),
            "has_scene_ref": has_scene_ref,
            "has_character_ref": has_char_ref,
            "has_image": bool(sb.composed_image or sb.first_frame_image),
            "missing_cast_images": missing_cast_images,
            "scene_bound": sb.scene_id is not None,
        },
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
    # 写回超限历史时长，避免分镜台反复读到 12s
    dirty = False
    for sb in rows:
        clamped = _clamp_storyboard_duration(sb.duration)
        if sb.duration != clamped:
            sb.duration = clamped
            dirty = True
    if dirty:
        db.commit()
    # 刷新运镜段落分组（同场景连续镜）
    refresh_episode_segments(db, episode_id, commit=True)
    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    return [storyboard_view(sb, db) for sb in rows]


@router.get("/segments")
def list_segments(episode_id: int, db: Session = Depends(get_db)) -> dict:
    refresh_episode_segments(db, episode_id, commit=True)
    return {"episode_id": episode_id, "segments": episode_segment_summaries(db, episode_id)}


@router.post("/{storyboard_id}/split")
async def split_long_storyboard(
    storyboard_id: int,
    body: StoryboardSplitRequest,
    db: Session = Depends(get_db),
) -> dict:
    """长镜拆解：一镜 → 连续 2–4 个 3–5 秒子镜，共享运镜段落。

    台词按子镜拆分后会清空旧 TTS，并对有台词的子镜自动重配本段语音。
    """
    ensure_active_user(db, body.username)
    try:
        result = split_storyboard(
            db,
            storyboard_id,
            parts=body.parts,
            use_llm=body.use_llm,
        )
    except StoryboardSplitError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LLMNotConfigured as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 避免 500 空白，前端能看到原因
        raise HTTPException(500, f"拆镜失败：{exc}") from exc

    # 已取消独立 TTS：拆镜只清图/旧配音元数据，语音在「定稿出片」时由视频模型生成
    result["message"] = (
        result.get("message")
        or "已拆成连续子镜；图已清空。请批量出图后「定稿出片」——台词将由视频模型直接生成语音。"
    )
    result["tts_results"] = []
    result["tts_ok"] = 0
    result["tts_fail"] = 0
    result["needs_tts"] = False
    result["needs_tts_ids"] = []

    # 返回本集全部分镜，便于前端整表刷新
    episode_id = None
    source = db.get(Storyboard, result["storyboard_ids"][0]) if result.get("storyboard_ids") else None
    if source is not None:
        episode_id = source.episode_id
    storyboards = []
    if episode_id is not None:
        storyboards = [
            storyboard_view(row, db)
            for row in db.scalars(
                select(Storyboard)
                .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
                .order_by(Storyboard.storyboard_number)
            ).all()
        ]
    return {**result, "storyboards": storyboards}


@router.post("/generate")
def generate_storyboards(
    req: StoryboardGenerateRequest,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
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
        shots = break_storyboards(db, script, req.temperature, episode_id=ep.id)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            raise HTTPException(
                502,
                f"LLM 鉴权失败（HTTP {code}）：API Key 无效或已过期，请到「设置 → 语言模型」更新并点「测试连接」",
            ) from e
        raise HTTPException(502, f"LLM 调用失败: HTTP {code}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}") from e
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"分镜结果解析失败: {e}") from e

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
        "storyboards": [storyboard_view(sb, db) for sb in rows],
        "warn": input_result.warn or output_result.warn,
        "hits": _hits(input_result, output_result),
    }


def _saved_image_url(outcome: GenerationOutcome) -> str | None:
    """优先返回落盘后的 /oss/ 永久地址，避免 Comfy /view 临时链导致多镜显示成同一张。"""
    if outcome.asset is not None and outcome.asset.url:
        return outcome.asset.url
    if outcome.result.image_path:
        return "/oss/" + Path(outcome.result.image_path).name
    return outcome.result.image_url


def _outcome_view(outcome: GenerationOutcome, *, storyboard: Storyboard | None = None) -> dict:
    payload = {
        "status": outcome.result.status,
        "image_url": _saved_image_url(outcome),
        "local_path": outcome.result.image_path,
        "prompt": outcome.prompt,
        "warn": outcome.compliance.warn,
        "warn_hits": [hit.__dict__ for hit in outcome.compliance.hits] if outcome.compliance.warn else [],
    }
    if storyboard is not None:
        payload["storyboard_id"] = storyboard.id
        payload["storyboard_number"] = storyboard.storyboard_number
        # 再读一次库里的成图，保证与列表接口一致
        if storyboard.composed_image:
            payload["image_url"] = storyboard.composed_image
    return payload


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
        sb = db.get(Storyboard, storyboard_id)
        return _outcome_view(outcome, storyboard=sb)
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
def update_storyboard(storyboard_id: int, body: StoryboardUpdate, db: Session = Depends(get_db)) -> dict:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise HTTPException(404, "分镜不存在")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "没有需要保存的修改")
    speaking_character_id = changes.get("speaking_character_id")
    if speaking_character_id is not None:
        episode = db.get(Episode, sb.episode_id)
        character = db.get(Character, speaking_character_id)
        if character is None or character.deleted_at is not None:
            raise HTTPException(404, "说话角色不存在")
        if episode is None or character.drama_id != episode.drama_id:
            raise HTTPException(400, "说话角色不属于当前剧本")
    for field, value in changes.items():
        if field == "reference_images":
            sb.reference_images = None if value is None else json.dumps(value, ensure_ascii=False)
        elif field == "duration":
            sb.duration = _clamp_storyboard_duration(value)
        else:
            setattr(sb, field, value)
    # 保存台词后自动绑定说话人（成片 <Picture N> / 口型主体）
    if "dialogue" in changes and changes.get("speaking_character_id") is None:
        try:
            from app.services.video_generation import infer_speaking_character_id

            sid = infer_speaking_character_id(db, sb)
            if sid:
                sb.speaking_character_id = sid
        except Exception:  # noqa: BLE001
            pass
    db.commit()
    db.refresh(sb)
    return storyboard_view(sb, db)


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
            sb_row = db.get(Storyboard, sb_id)
            results.append({
                "storyboard_id": sb_id,
                "storyboard_number": sb_row.storyboard_number if sb_row else None,
                "status": "completed",
                "image_url": (sb_row.composed_image if sb_row and sb_row.composed_image else _saved_image_url(outcome)),
            })
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
        updated = polish_prompts(db, shots, body.temperature, episode_id=rows[0].episode_id)
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
