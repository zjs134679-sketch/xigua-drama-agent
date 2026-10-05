"""角色音色绑定：结构化声线匹配 + 冲突消解 + edge-tts 音色库。

行业短剧流水线共性（自研实现，无第三方 Skill 拷贝）：
- 声线维度：性别 / 年龄段 / 角色定位
- 主要角色尽量不撞同一 voice_id
- LLM 失败时规则回退
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AiVoice, Character, Storyboard
from app.services.agents.script_agent import load_skill
from app.services.llm.client import chat_text, resolve_llm


# 真实可合成的 edge-tts 中文嗓音
EDGE_VOICES = (
    {"voice_id": "zh-CN-YunxiNeural", "voice_name": "云希·青年男声", "description": "清亮有活力，少年/青年男主", "gender": "male", "age": "young", "tags": "主角 青年 清亮"},
    {"voice_id": "zh-CN-YunjianNeural", "voice_name": "云健·沉稳男声", "description": "低沉浑厚，硬汉/将领/中年男", "gender": "male", "age": "mid", "tags": "将领 硬汉 沉稳 中年"},
    {"voice_id": "zh-CN-YunyangNeural", "voice_name": "云扬·磁性男声", "description": "播音腔，旁白/长者/权威", "gender": "male", "age": "elder", "tags": "旁白 长者 权威 老年"},
    {"voice_id": "zh-CN-YunxiaNeural", "voice_name": "云夏·少年音", "description": "偏年轻、少年感", "gender": "male", "age": "teen", "tags": "少年 年轻"},
    {"voice_id": "zh-CN-XiaoxiaoNeural", "voice_name": "晓晓·温婉女声", "description": "标准温暖女声，百搭女主", "gender": "female", "age": "mid", "tags": "女主 成熟 温暖"},
    {"voice_id": "zh-CN-XiaoyiNeural", "voice_name": "晓伊·甜美女声", "description": "明亮活泼，少女", "gender": "female", "age": "young", "tags": "少女 活泼"},
    {"voice_id": "zh-CN-liaoning-XiaobeiNeural", "voice_name": "晓北·东北女声", "description": "东北口音，市井/喜剧", "gender": "female", "age": "mid", "tags": "喜剧 市井 方言"},
    {"voice_id": "zh-CN-shaanxi-XiaoniNeural", "voice_name": "晓妮·陕西女声", "description": "陕西口音，地域角色", "gender": "female", "age": "mid", "tags": "方言 地域"},
)

LEGACY_PRESET_TO_EDGE = {
    "preset_male_young": "zh-CN-YunxiNeural",
    "preset_male_steady": "zh-CN-YunjianNeural",
    "preset_female_sweet": "zh-CN-XiaoyiNeural",
    "preset_female_mature": "zh-CN-XiaoxiaoNeural",
    "preset_old_male": "zh-CN-YunyangNeural",
}

# 音色元数据（用于冲突消解打分）
_VOICE_META: dict[str, dict[str, str]] = {
    v["voice_id"]: {"gender": v["gender"], "age": v["age"], "tags": v.get("tags", "")}
    for v in EDGE_VOICES
}


@dataclass(frozen=True)
class VoiceProfile:
    gender: str  # male | female | unknown
    age: str  # teen | young | mid | elder | unknown
    role_type: str  # lead | support | villain | narrator | extra
    priority: int  # 越高越优先独占音色


def seed_preset_voices(db: Session) -> None:
    """确保真实 edge 音色入库；清掉旧抽象预设并把绑定迁移到对应真实嗓音（幂等）。"""
    existing = {v.voice_id: v for v in db.scalars(select(AiVoice)).all()}
    changed = False
    for voice in EDGE_VOICES:
        row = {k: voice[k] for k in ("voice_id", "voice_name", "description")}
        if voice["voice_id"] not in existing:
            db.add(AiVoice(language="zh-CN", provider="edge", **row))
            changed = True
    legacy = [v for vid, v in existing.items() if vid.startswith("preset_")]
    if legacy:
        for ch in db.scalars(select(Character).where(Character.voice_style.like("preset_%"))).all():
            ch.voice_style = LEGACY_PRESET_TO_EDGE.get(ch.voice_style or "", "zh-CN-YunyangNeural")
            ch.voice_provider = "edge"
        for v in legacy:
            db.delete(v)
        changed = True
    if changed:
        db.commit()


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1 :]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        return json.loads(text[start : end + 1])


def profile_for_character(character: Character) -> VoiceProfile:
    """从角色字段推断声线画像（结构化，供匹配与冲突消解）。"""
    traits = " ".join(
        value
        for value in (
            character.name,
            character.role,
            character.description,
            character.personality,
            character.appearance,
        )
        if value
    )
    gender = "unknown"
    if any(k in traits for k in ("女", "娘", "姐", "妈", "母", "妃", "公主", "夫人", "少女", "姑娘")):
        gender = "female"
    elif any(k in traits for k in ("男", "爷", "爹", "父", "哥", "弟", "将", "王", "公", "侯", "兵", "士")):
        gender = "male"
    # 常见称呼补刀
    if gender == "unknown" and any(k in traits for k in ("老人", "老头", "老爷", "大叔", "大爷", "老汉")):
        gender = "male"
    if gender == "unknown" and any(k in traits for k in ("老太", "老妇", "婆婆", "大娘", "奶奶")):
        gender = "female"
    if gender == "unknown" and any(k in (character.name or "") for k in ("娘", "妃", "姬", "瑶", "雪", "婷", "芳")):
        gender = "female"

    age = "unknown"
    if any(k in traits for k in ("老", "老年", "长者", "爷爷", "祖母", "太爷", "老太")):
        age = "elder"
    elif any(k in traits for k in ("少年", "孩", "幼", "童")):
        age = "teen"
    elif any(k in traits for k in ("少女", "青年", "年轻", "少主")):
        age = "young"
    elif any(k in traits for k in ("中年", "将领", "将军", "统帅", "王")):
        age = "mid"

    role_type = "support"
    role = (character.role or "") + traits
    if any(k in role for k in ("旁白", "解说", "画外")):
        role_type = "narrator"
        priority = 90
    elif any(k in role for k in ("主角", "男主", "女主", "主人公")):
        role_type = "lead"
        priority = 100
    elif any(k in role for k in ("反派", "敌", "反")):
        role_type = "villain"
        priority = 80
    elif any(k in role for k in ("龙套", "群演", "路人", "士兵", "兵")):
        role_type = "extra"
        priority = 20
    else:
        priority = 50
        if "重要" in role or "配角" in role:
            priority = 60

    return VoiceProfile(gender=gender, age=age, role_type=role_type, priority=priority)


def _score_voice(profile: VoiceProfile, voice: AiVoice) -> int:
    meta = _VOICE_META.get(voice.voice_id or "", {})
    score = 0
    vg, va = meta.get("gender", ""), meta.get("age", "")
    tags = (meta.get("tags") or "") + " " + (voice.description or "") + " " + (voice.voice_name or "")
    if profile.gender != "unknown" and vg == profile.gender:
        score += 50
    elif profile.gender != "unknown" and vg and vg != profile.gender:
        score -= 80
    if profile.age != "unknown" and va == profile.age:
        score += 30
    elif profile.age == "elder" and va == "mid":
        score += 10
    elif profile.age == "young" and va == "teen":
        score += 15
    if profile.role_type == "narrator" and ("旁白" in tags or "权威" in tags or "播音" in tags):
        score += 40
    if profile.role_type == "lead" and ("主角" in tags or "清亮" in tags or "温暖" in tags):
        score += 15
    if profile.role_type == "villain" and ("硬汉" in tags or "沉稳" in tags or "将领" in tags):
        score += 15
    if profile.role_type == "extra":
        score += 5  # 龙套谁都行
    return score


def _preferred_voice_id(character: Character) -> str:
    profile = profile_for_character(character)
    # 兼容旧接口：返回最高分 edge id
    best = "zh-CN-YunxiNeural"
    best_score = -10_000
    for v in EDGE_VOICES:
        fake = AiVoice(
            voice_id=v["voice_id"],
            voice_name=v["voice_name"],
            description=v["description"],
            provider="edge",
        )
        s = _score_voice(profile, fake)
        if s > best_score:
            best_score = s
            best = v["voice_id"]
    return best


def _resolve_conflicts(
    characters: list[Character],
    preferred: dict[int, AiVoice],
    voices: list[AiVoice],
    *,
    reserved: set[str] | None = None,
) -> dict[int, AiVoice]:
    """按优先级贪心分配，尽量让高优先级角色独占音色。"""
    reserved = set(reserved or ())
    by_id = {v.voice_id: v for v in voices}
    ranked = sorted(
        characters,
        key=lambda c: (-profile_for_character(c).priority, c.id or 0),
    )
    used: set[str] = set(reserved)
    result: dict[int, AiVoice] = {}

    def _gender_ok(profile: VoiceProfile, voice: AiVoice) -> bool:
        if profile.gender == "unknown":
            return True
        vg = _VOICE_META.get(voice.voice_id or "", {}).get("gender", "")
        return not vg or vg == profile.gender

    for ch in ranked:
        pref = preferred.get(ch.id)
        profile = profile_for_character(ch)
        candidates = sorted(
            voices,
            key=lambda v: (
                0 if _gender_ok(profile, v) else 1,
                0 if (pref and v.voice_id == pref.voice_id) else 1,
                -_score_voice(profile, v),
                0 if v.voice_id not in used else 1,
                v.id or 0,
            ),
        )
        chosen = None
        # 1) 未占用 + 性别对
        for v in candidates:
            if v.voice_id not in used and _gender_ok(profile, v):
                chosen = v
                break
        # 2) 可占用 + 性别对（龙套/声线不够时允许撞声）
        if chosen is None:
            for v in candidates:
                if _gender_ok(profile, v):
                    chosen = v
                    break
        # 3) 兜底
        if chosen is None:
            chosen = pref if pref and _gender_ok(profile, pref) else candidates[0]
        result[ch.id] = chosen
        if profile.priority >= 50:  # 主角/重要配角占用
            used.add(chosen.voice_id)
    return result


def _fallback_assignments(
    characters: list[Character],
    voices: list[AiVoice],
    *,
    reserved: set[str] | None = None,
) -> dict[int, AiVoice]:
    by_id = {voice.voice_id: voice for voice in voices}
    preferred: dict[int, AiVoice] = {}
    for character in characters:
        vid = _preferred_voice_id(character)
        preferred[character.id] = by_id.get(vid) or voices[0]
    return _resolve_conflicts(characters, preferred, voices, reserved=reserved)


def _llm_assignments(
    db: Session,
    characters: list[Character],
    voices: list[AiVoice],
) -> dict[int, AiVoice]:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("xg_voice_match")
    payload = {
        "characters": [
            {
                "id": character.id,
                "name": character.name,
                "role": character.role,
                "description": character.description,
                "personality": character.personality,
                "appearance": character.appearance,
                "profile": {
                    "gender": profile_for_character(character).gender,
                    "age": profile_for_character(character).age,
                    "role_type": profile_for_character(character).role_type,
                    "priority": profile_for_character(character).priority,
                },
            }
            for character in characters
        ],
        "voices": [
            {
                "voice_id": voice.voice_id,
                "voice_name": voice.voice_name,
                "description": voice.description,
                "provider": voice.provider,
            }
            for voice in voices
        ],
        "rules": [
            "主要角色尽量使用不同 voice_id",
            "性别要匹配",
            "旁白/长者优先云扬，少女优先晓伊，硬汉将领优先云健",
        ],
    }
    messages = [
        {
            "role": "system",
            "content": (
                skill
                + "\n\n只输出 JSON：{\"assignments\":[{\"character_id\":1,\"voice_id\":\"...\"}]}。"
                "每个角色只能使用给定音色；重要角色不要共用同一 voice_id。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    raw = chat_text(
        messages,
        base_url,
        api_key,
        model,
        temperature=0.2,
        response_format={"type": "json_object"},
        timeout=60.0,
    )
    data = _parse_json(raw if isinstance(raw, str) else str(raw))
    voice_by_id = {voice.voice_id: voice for voice in voices}
    character_ids = {character.id for character in characters}
    assignments: dict[int, AiVoice] = {}
    for item in data.get("assignments") or []:
        try:
            character_id = int(item.get("character_id"))
        except (TypeError, ValueError):
            continue
        voice = voice_by_id.get(item.get("voice_id"))
        if character_id in character_ids and voice is not None:
            assignments[character_id] = voice
    return assignments


def invalidate_tts_for_character(db: Session, character_id: int) -> int:
    """角色换绑音色后，清空其作为说话人的分镜配音，提示重生成。"""
    rows = db.scalars(
        select(Storyboard).where(
            Storyboard.speaking_character_id == character_id,
            Storyboard.deleted_at.is_(None),
        )
    ).all()
    n = 0
    for sb in rows:
        if sb.tts_audio_url:
            sb.tts_audio_url = None
            n += 1
    return n


def assign_character_voices(db: Session, drama_id: int, *, force: bool = False) -> list[dict]:
    """为角色绑定 edge 音色。默认只处理未绑定；force=True 时全部重绑。"""
    seed_preset_voices(db)
    all_chars = db.scalars(
        select(Character)
        .where(Character.drama_id == drama_id, Character.deleted_at.is_(None))
        .order_by(Character.id)
    ).all()
    if not force:
        characters = [c for c in all_chars if not (c.voice_style or "").strip()]
        reserved = {
            (c.voice_style or "").strip()
            for c in all_chars
            if (c.voice_style or "").strip()
        }
    else:
        characters = list(all_chars)
        reserved = set()
    if not characters:
        return []
    voices = db.scalars(select(AiVoice).order_by(AiVoice.id)).all()
    if not voices:
        seed_preset_voices(db)
        voices = db.scalars(select(AiVoice).order_by(AiVoice.id)).all()
    if not voices:
        raise LookupError("没有可用音色，请重启后端以初始化音色库")

    fallback = _fallback_assignments(characters, voices, reserved=reserved)
    try:
        llm_map = _llm_assignments(db, characters, voices)
        # LLM 结果再过冲突消解
        preferred = {**fallback, **llm_map}
        assignments = _resolve_conflicts(characters, preferred, voices, reserved=reserved)
    except Exception:  # noqa: BLE001
        assignments = fallback

    results: list[dict] = []
    for character in characters:
        old_voice = (character.voice_style or "").strip()
        voice = assignments.get(character.id) or fallback.get(character.id) or voices[0]
        character.voice_style = voice.voice_id
        character.voice_provider = voice.provider or "edge"
        if old_voice and old_voice != voice.voice_id:
            invalidate_tts_for_character(db, character.id)
        profile = profile_for_character(character)
        results.append(
            {
                "character_id": character.id,
                "character_name": character.name,
                "voice_id": voice.voice_id,
                "voice_name": voice.voice_name,
                "voice_provider": character.voice_provider,
                "profile": {
                    "gender": profile.gender,
                    "age": profile.age,
                    "role_type": profile.role_type,
                    "priority": profile.priority,
                },
            }
        )
    db.commit()
    return results


def list_unvoiced_characters(db: Session, drama_id: int) -> list[Character]:
    return list(
        db.scalars(
            select(Character).where(
                Character.drama_id == drama_id,
                Character.deleted_at.is_(None),
                (Character.voice_style.is_(None)) | (Character.voice_style == ""),
            )
        ).all()
    )
