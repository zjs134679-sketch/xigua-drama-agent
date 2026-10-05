"""StyleComposer —— 按项目 Style Bible + 阶段组装 StyleContract。

西瓜原创注入入口；供编剧 / 分镜 / 出图 / 视频 / 审核调用。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain import ArtStyle, Drama, Episode
from app.services.art_style_pack import (
    extract_video_style_tags,
    read_pack_file,
    skill_key_for_style,
)
from app.services.style_contract import (
    STAGE_AUDIT,
    STAGE_CAST,
    STAGE_IDENTITY,
    STAGE_SCENE_ENV,
    STAGE_SCRIPT,
    STAGE_SHOT_BREAK,
    STAGE_SHOT_IMAGE,
    STAGE_VIDEO,
    StyleContract,
    check_compat,
    extract_anchors_from_manual,
    extract_bans_from_manual,
    load_narrative_hint,
    load_pacing_hint,
    load_platform,
    parse_bible,
)


class StyleConflictError(ValueError):
    """画风 × 节奏取向禁止组合。"""


def _meta_from_skill(skill_key: str | None) -> dict[str, Any]:
    """从画风 SKILL.md frontmatter 读取可选机读字段。"""
    if not skill_key:
        return {}
    from app.services.art_style_seed import _parse_frontmatter

    text = read_pack_file(skill_key, "SKILL.md")
    if not text:
        return {}
    meta, _ = _parse_frontmatter(text)
    return meta


def _parse_list_field(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    # [a, b] or a,b
    text = text.strip("[]")
    return [p.strip().strip("\"'") for p in text.split(",") if p.strip().strip("\"'")]


def _parse_shot_bias(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        out: dict[str, float] = {}
        for k, v in value.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    if not value:
        return {}
    # close:0.45 mid:0.35 wide:0.2
    out = {}
    for part in str(value).replace(",", " ").split():
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        try:
            out[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return out


def _stage_notes(stage: str, meta: dict[str, Any]) -> str:
    if stage == STAGE_IDENTITY:
        return (
            "【定装阶段】优先中性站姿与可读五官/服装；光线均匀平实，锁同一身份特征；"
            "背景简洁勿抢主体。不要使用强烈戏剧性光效破坏身份识别。"
        )
    if stage == STAGE_SCENE_ENV:
        return (
            "【场景造景】纯环境建立镜头 empty establishing plate；"
            "只描述地点、建筑、陈设、道具、天气、光线与空间层次；"
            "画面中绝对不要出现任何人、士兵、行人、剪影、人脸或人体部位。"
        )
    if stage == STAGE_SHOT_IMAGE:
        return (
            "【分镜图阶段】允许按情绪使用侧光/轮廓光/冷暖对比；"
            "不要套用定装的「平光中性」句式；保持画风锚词与角色外形一致。"
        )
    if stage == STAGE_VIDEO:
        return "【视频阶段】动作时间片不超过本镜秒数；运镜可写清起幅落幅；保持画风标签。"
    if stage == STAGE_SHOT_BREAK:
        return "【分镜拆解】一镜一事；image_prompt/video_prompt 中文；融入画风气氛词，勿中英混杂堆砌。"
    if stage == STAGE_SCRIPT:
        return "【编剧阶段】对白可拍、动作外化；节奏服从当前取向；不要写超出单镜可拍长度的冗长独白块。"
    if stage == STAGE_CAST:
        return "【资产提取】角色外貌与场景需可出图；记录显著视觉母题（服装主色、标志道具）。"
    if stage == STAGE_AUDIT:
        return "【风格审核】检查锚词覆盖、禁用词、景别是否极端失衡、是否混入跨画风材质描述。"
    # optional meta stage_rules string
    return str(meta.get("stage_notes") or "")


def _direction_hint(skill_key: str | None) -> str:
    if not skill_key:
        return ""
    text = read_pack_file(skill_key, "direction/storyboard.md")
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return " ".join(lines[:8])[:280]


def drama_bible(drama: Drama | None) -> dict[str, Any]:
    if drama is None:
        return {}
    raw = getattr(drama, "style_bible", None)
    data = parse_bible(raw)
    if data:
        return data
    # 降级：从旧字段拼
    bible: dict[str, Any] = {}
    if drama.style:
        bible["legacy_style"] = drama.style
    if drama.genre:
        bible["narrative_tag"] = drama.genre
    return bible


def save_bible(drama: Drama, bible: dict[str, Any]) -> None:
    drama.style_bible = json.dumps(bible, ensure_ascii=False)


def resolve_art_style(db: Session | None, bible: dict[str, Any], art_style_id: int | None = None) -> ArtStyle | None:
    if db is None:
        return None
    sid = art_style_id if art_style_id is not None else bible.get("art_style_id")
    if sid is not None:
        try:
            return db.get(ArtStyle, int(sid))
        except (TypeError, ValueError):
            pass
    name = bible.get("visual_name")
    if name:
        from sqlalchemy import select

        row = db.scalars(select(ArtStyle).where(ArtStyle.name == name)).first()
        if row:
            return row
    return None


def compose(
    *,
    db: Session | None = None,
    drama: Drama | None = None,
    stage: str = STAGE_SHOT_IMAGE,
    art_style_id: int | None = None,
    art_style: ArtStyle | None = None,
    bible_override: dict[str, Any] | None = None,
    enforce_forbid: bool = False,
) -> StyleContract:
    """组装契约。enforce_forbid=True 时禁止组合抛 StyleConflictError。"""
    bible = bible_override if bible_override is not None else drama_bible(drama)
    style = art_style or resolve_art_style(db, bible, art_style_id)
    skill_key = skill_key_for_style(style) if style else bible.get("visual_pack")
    meta = _meta_from_skill(skill_key if isinstance(skill_key, str) else None)

    pacing = str(bible.get("pacing_profile") or "pace_balanced")
    narrative = bible.get("narrative_tag") or bible.get("genre")
    aspect = str(bible.get("aspect") or load_platform().get("default_aspect") or "9:16")

    anchors = _parse_list_field(meta.get("anchors_zh"))
    if not anchors and style and style.constraint_manual:
        anchors = extract_anchors_from_manual(style.constraint_manual)
    bans = _parse_list_field(meta.get("bans"))
    if not bans and style and style.constraint_manual:
        bans = extract_bans_from_manual(style.constraint_manual)
    # 全局默认禁用
    for w in ("水印", "二维码", "UI边框", "字幕条"):
        if w not in bans:
            bans.append(w)

    shot_bias = _parse_shot_bias(meta.get("shot_bias"))
    if not shot_bias:
        shot_bias = {"近景": 0.4, "中景": 0.35, "全景": 0.15, "特写": 0.1}

    level, msg = check_compat(pacing, skill_key if isinstance(skill_key, str) else None)
    if enforce_forbid and level == "forbid":
        raise StyleConflictError(msg or "画风与节奏取向不兼容")

    video_tags = ""
    if skill_key and isinstance(skill_key, str):
        video_tags = extract_video_style_tags(skill_key, prefer="zh") or ""

    return StyleContract(
        version=int(bible.get("version") or 1),
        visual_pack=skill_key if isinstance(skill_key, str) else None,
        visual_name=(style.name if style else None) or bible.get("visual_name"),
        narrative_tag=str(narrative) if narrative else None,
        pacing_profile=pacing,
        aspect=aspect,
        art_style_id=style.id if style else (
            int(bible["art_style_id"]) if bible.get("art_style_id") is not None else None
        ),
        must_include=anchors,
        must_exclude=bans,
        shot_bias=shot_bias,
        prompt_suffix=(style.prompt_suffix if style else "") or str(meta.get("prompt_suffix") or ""),
        stage=stage,
        stage_notes=_stage_notes(stage, meta),
        direction_hint=_direction_hint(skill_key if isinstance(skill_key, str) else None),
        pacing_hint=load_pacing_hint(pacing),
        narrative_hint=load_narrative_hint(str(narrative) if narrative else None),
        video_tags=video_tags,
        compat_level=level,
        compat_message=msg,
    )


def compose_for_episode(
    db: Session,
    episode_id: int,
    stage: str,
    *,
    art_style_id: int | None = None,
    enforce_forbid: bool = False,
) -> StyleContract:
    ep = db.get(Episode, episode_id)
    drama = db.get(Drama, ep.drama_id) if ep else None
    return compose(
        db=db,
        drama=drama,
        stage=stage,
        art_style_id=art_style_id,
        enforce_forbid=enforce_forbid,
    )


def compose_for_drama(
    db: Session,
    drama_id: int,
    stage: str,
    *,
    art_style_id: int | None = None,
    enforce_forbid: bool = False,
) -> StyleContract:
    drama = db.get(Drama, drama_id)
    return compose(
        db=db,
        drama=drama,
        stage=stage,
        art_style_id=art_style_id,
        enforce_forbid=enforce_forbid,
    )


def validate_bible_dict(bible: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    """规范化并校验；forbid 时抛 StyleConflictError。"""
    from app.services.style_contract import VALID_PACING

    pacing = str(bible.get("pacing_profile") or "pace_balanced")
    if pacing not in VALID_PACING:
        pacing = "pace_balanced"
    aspect = str(bible.get("aspect") or "9:16")
    if aspect not in ("9:16", "16:9"):
        aspect = "9:16"
    art_style_id = bible.get("art_style_id")
    style = None
    visual_pack = bible.get("visual_pack")
    visual_name = bible.get("visual_name")
    if db is not None and art_style_id is not None:
        style = db.get(ArtStyle, int(art_style_id))
        if style:
            visual_name = style.name
            visual_pack = skill_key_for_style(style) or visual_pack
    level, msg = check_compat(pacing, visual_pack if isinstance(visual_pack, str) else None)
    if level == "forbid":
        raise StyleConflictError(msg or "画风与节奏取向不兼容，请更换组合")
    out = {
        "version": int(bible.get("version") or 1),
        "visual_pack": visual_pack,
        "visual_name": visual_name,
        "narrative_tag": bible.get("narrative_tag") or bible.get("genre"),
        "pacing_profile": pacing,
        "aspect": aspect,
        "art_style_id": int(art_style_id) if art_style_id is not None else None,
        "compat_level": level,
        "compat_message": msg,
    }
    return out
