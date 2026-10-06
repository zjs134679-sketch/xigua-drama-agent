from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.services.license_gate import require_valid_license
from app.models.domain import (
    Character,
    Drama,
    Episode,
    EpisodeCharacter,
    EpisodeScene,
    Prop,
    Scene,
    Storyboard,
    StoryboardCharacter,
)
from app.schemas.project import (
    AssetPromptUpdate,
    DramaCreate,
    EpisodeCreate,
    NovelImportRequest,
    StyleBibleIn,
)
from app.services.asset_generation import build_character_prompt
from app.services.compliance import check, enforce
from app.services.novel_split import split_novel
from app.services.project_deletion import delete_project
from app.services.style_composer import (
    StyleConflictError,
    drama_bible,
    save_bible,
    validate_bible_dict,
)
from app.services.style_contract import list_pacing_profiles, list_story_types
router = APIRouter(prefix="/projects", tags=["projects"])


def drama_view(d: Drama) -> dict:
    bible = drama_bible(d)
    return {
        "id": d.id,
        "title": d.title,
        "description": d.description,
        "genre": d.genre,
        "style": d.style,
        "status": d.status,
        "total_episodes": d.total_episodes,
        "has_director_manual": bool(getattr(d, "director_manual", None)),
        "has_visual_manual": bool(getattr(d, "visual_manual", None)),
        "style_bible": bible or None,
    }


def episode_view(e: Episode, with_content: bool = False) -> dict:
    v = {
        "id": e.id,
        "drama_id": e.drama_id,
        "episode_number": e.episode_number,
        "title": e.title,
        "status": e.status,
        "has_content": bool(e.content),
        "has_script": bool(e.script_content),
    }
    if with_content:
        v["content"] = e.content
        v["script_content"] = e.script_content
    return v


@router.post("")
def create_drama(body: DramaCreate, db: Session = Depends(get_db)) -> dict:
    d = Drama(
        title=body.title,
        description=body.description,
        genre=body.genre or body.narrative_tag,
        style=body.style,
    )
    raw_bible = {
        "version": 1,
        "art_style_id": body.art_style_id,
        "narrative_tag": body.narrative_tag or body.genre,
        "pacing_profile": body.pacing_profile or "pace_balanced",
        "aspect": body.aspect or "9:16",
    }
    try:
        bible = validate_bible_dict(raw_bible, db)
    except StyleConflictError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 同步显示名到 style 字段便于旧 UI
    if bible.get("visual_name"):
        d.style = str(bible["visual_name"])
    if bible.get("narrative_tag"):
        d.genre = str(bible["narrative_tag"])
    save_bible(d, bible)
    db.add(d)
    db.commit()
    db.refresh(d)
    return drama_view(d)


@router.get("/style-options")
def style_options() -> dict:
    """节奏取向 + 故事类型目录（建项用）。"""
    return {
        "pacing_profiles": list_pacing_profiles(),
        "story_types": list_story_types(),
        "aspect_options": ["9:16", "16:9"],
    }


@router.get("/{drama_id}/style-bible")
def get_style_bible(drama_id: int, db: Session = Depends(get_db)) -> dict:
    d = db.get(Drama, drama_id)
    if not d or d.deleted_at is not None:
        raise HTTPException(404, "项目不存在")
    bible = drama_bible(d)
    return {"drama_id": drama_id, "style_bible": bible}


@router.put("/{drama_id}/style-bible")
def put_style_bible(drama_id: int, body: StyleBibleIn, db: Session = Depends(get_db)) -> dict:
    d = db.get(Drama, drama_id)
    if not d or d.deleted_at is not None:
        raise HTTPException(404, "项目不存在")
    merged = {**drama_bible(d), **body.model_dump(exclude_unset=True)}
    try:
        bible = validate_bible_dict(merged, db)
    except StyleConflictError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_bible(d, bible)
    if bible.get("visual_name"):
        d.style = str(bible["visual_name"])
    if bible.get("narrative_tag"):
        d.genre = str(bible["narrative_tag"])
    db.commit()
    db.refresh(d)
    return drama_view(d)


@router.get("")
def list_dramas(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Drama).where(Drama.deleted_at.is_(None)).order_by(Drama.id.desc())).all()
    return [drama_view(d) for d in rows]


@router.get("/{drama_id}")
def get_drama(drama_id: int, db: Session = Depends(get_db)) -> dict:
    d = db.get(Drama, drama_id)
    if not d or d.deleted_at is not None:
        raise HTTPException(404, "项目不存在")
    return drama_view(d)


@router.delete("/{drama_id}")
def remove_drama(drama_id: int, db: Session = Depends(get_db)) -> dict:
    """Permanently delete a project, its related records, and unshared local files."""
    try:
        return delete_project(db, drama_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{drama_id}/episodes")
def create_episode(drama_id: int, body: EpisodeCreate, db: Session = Depends(get_db)) -> dict:
    if not db.get(Drama, drama_id):
        raise HTTPException(404, "项目不存在")
    e = Episode(
        drama_id=drama_id,
        episode_number=body.episode_number,
        title=body.title,
        content=body.content,
    )
    db.add(e)
    db.commit()
    return episode_view(e, with_content=True)


@router.post("/{drama_id}/import-novel")
def import_novel(
    drama_id: int,
    body: NovelImportRequest,
    db: Session = Depends(get_db),
    lic: dict = Depends(require_valid_license),
) -> dict:
    """整篇小说 → 合规 → 自动分集（按章/按长度）→ 批量建分集。"""
    # E1：身份取自票据，不再信任请求体自填的 username
    username = enforce.ticket_username(lic) or body.username
    ensure_active_user(db, username)
    drama = db.get(Drama, drama_id)
    if not drama:
        raise HTTPException(404, "项目不存在")

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "请粘贴小说原文")

    result = check(text)
    if result.blocked:
        audit = enforce.record_violation(db, username, result, "novel_import", lic=lic)
        raise HTTPException(
            status_code=451,
            detail={
                "blocked": True,
                "level": "red",
                "message": "小说原文触发红线，已拦截并记录",
                "hits": [hit.__dict__ for hit in result.hits],
                "violation_count": audit["violation_count"],
                "banned": audit["banned"],
            },
        )

    chapters = split_novel(text, body.max_chars)
    if not chapters:
        raise HTTPException(400, "未能从原文中切分出内容")

    start = db.scalars(
        select(Episode.episode_number)
        .where(Episode.drama_id == drama_id)
        .order_by(Episode.episode_number.desc())
    ).first() or 0

    created: list[Episode] = []
    for offset, chapter in enumerate(chapters, start=1):
        episode = Episode(
            drama_id=drama_id,
            episode_number=start + offset,
            title=chapter["title"],
            content=chapter["content"],
        )
        db.add(episode)
        created.append(episode)
    db.commit()

    return {
        "drama_id": drama_id,
        "created": len(created),
        "warn": result.warn,
        "episodes": [episode_view(e) for e in created],
    }


@router.get("/{drama_id}/episodes")
def list_episodes(drama_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Episode)
        .where(Episode.drama_id == drama_id, Episode.deleted_at.is_(None))
        .order_by(Episode.episode_number)
    ).all()
    return [episode_view(e) for e in rows]


@router.get("/episodes/{episode_id}")
def get_episode(episode_id: int, db: Session = Depends(get_db)) -> dict:
    e = db.get(Episode, episode_id)
    if not e or e.deleted_at is not None:
        raise HTTPException(404, "分集不存在")
    return episode_view(e, with_content=True)


@router.get("/{drama_id}/characters")
def list_characters(
    drama_id: int,
    episode_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """列出角色。传 episode_id 时只返回本集相关（分集关联 ∪ 分镜出镜），供流水线标绿。"""
    q = select(Character).where(Character.drama_id == drama_id, Character.deleted_at.is_(None))
    if episode_id is not None:
        ep = db.get(Episode, episode_id)
        if ep is None or ep.deleted_at is not None or ep.drama_id != drama_id:
            raise HTTPException(404, "分集不存在或不属于该项目")
        linked = set(
            db.scalars(
                select(EpisodeCharacter.character_id).where(EpisodeCharacter.episode_id == episode_id)
            ).all()
        )
        # 分镜表里实际出镜的角色也算本集需要
        sb_ids = list(
            db.scalars(
                select(Storyboard.id).where(
                    Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None)
                )
            ).all()
        )
        if sb_ids:
            linked |= set(
                db.scalars(
                    select(StoryboardCharacter.character_id).where(
                        StoryboardCharacter.storyboard_id.in_(sb_ids)
                    )
                ).all()
            )
        if linked:
            q = q.where(Character.id.in_(linked))
        else:
            # 本集尚未关联任何角色：返回空，避免用全剧龙套卡住流水线
            return []
    rows = db.scalars(q.order_by(Character.id)).all()
    return [
        {"id": c.id, "name": c.name, "role": c.role, "appearance": c.appearance,
         "personality": c.personality, "description": c.description, "image_url": c.image_url,
         "voice_id": c.voice_style, "voice_provider": c.voice_provider,
         "view_type": c.view_type or "turnaround_head",
         # 可编辑出图提示词：已存优先，否则给一个可改的自动建议
         "image_prompt": c.image_prompt or build_character_prompt(c, None, None, view_type=c.view_type or "turnaround_head")}
        for c in rows
    ]


@router.patch("/characters/{character_id}")
def update_character_prompt(character_id: int, body: AssetPromptUpdate, db: Session = Depends(get_db)) -> dict:
    c = db.get(Character, character_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(404, "角色不存在")
    c.image_prompt = body.prompt
    db.commit()
    return {"id": c.id, "image_prompt": c.image_prompt}


@router.patch("/characters/{character_id}/voice")
def update_character_voice_removed(character_id: int):
    raise HTTPException(410, "已取消独立音色/TTS。对白在「定稿出片」时由视频模型生成语音。")


@router.post("/{drama_id}/assign-voices")
def assign_voices_removed(drama_id: int):
    raise HTTPException(410, "已取消一键绑定音色。请直接出图后定稿出片，模型生成语音。")


@router.get("/{drama_id}/scenes")
def list_scenes(
    drama_id: int,
    episode_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """列出场景。传 episode_id 时只返回本集相关（分集关联 ∪ 分镜用到的场景）。"""
    q = select(Scene).where(Scene.drama_id == drama_id, Scene.deleted_at.is_(None))
    if episode_id is not None:
        ep = db.get(Episode, episode_id)
        if ep is None or ep.deleted_at is not None or ep.drama_id != drama_id:
            raise HTTPException(404, "分集不存在或不属于该项目")
        linked = set(
            db.scalars(select(EpisodeScene.scene_id).where(EpisodeScene.episode_id == episode_id)).all()
        )
        used = set(
            db.scalars(
                select(Storyboard.scene_id).where(
                    Storyboard.episode_id == episode_id,
                    Storyboard.deleted_at.is_(None),
                    Storyboard.scene_id.is_not(None),
                )
            ).all()
        )
        linked |= {int(x) for x in used if x is not None}
        if linked:
            q = q.where(Scene.id.in_(linked))
        else:
            return []
    rows = db.scalars(q.order_by(Scene.id)).all()
    return [
        {"id": s.id, "location": s.location, "time": s.time, "prompt": s.prompt,
         "status": s.status, "image_url": s.image_url}
        for s in rows
    ]


@router.patch("/scenes/{scene_id}")
def update_scene_prompt(scene_id: int, body: AssetPromptUpdate, db: Session = Depends(get_db)) -> dict:
    s = db.get(Scene, scene_id)
    if s is None or s.deleted_at is not None:
        raise HTTPException(404, "场景不存在")
    s.prompt = body.prompt
    db.commit()
    return {"id": s.id, "prompt": s.prompt}


@router.get("/{drama_id}/props")
def list_props(drama_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Prop).where(Prop.drama_id == drama_id, Prop.deleted_at.is_(None)).order_by(Prop.id)
    ).all()
    return [
        {"id": p.id, "name": p.name, "type": p.type, "description": p.description,
         "prompt": p.prompt, "image_url": p.image_url}
        for p in rows
    ]


@router.patch("/props/{prop_id}")
def update_prop_prompt(prop_id: int, body: AssetPromptUpdate, db: Session = Depends(get_db)) -> dict:
    prop = db.get(Prop, prop_id)
    if prop is None or prop.deleted_at is not None:
        raise HTTPException(404, "道具不存在")
    prop.prompt = body.prompt
    db.commit()
    return {"id": prop.id, "prompt": prop.prompt}
