from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AiVoice, Character
from app.services.agents.script_agent import load_skill
from app.services.llm.client import LLMNotConfigured, chat, resolve_llm


# 真实可合成的 edge-tts 中文嗓音（voice_id 直接用 edge ShortName，可绑定 / 试听 / 出配音）
EDGE_VOICES = (
    {"voice_id": "zh-CN-YunxiNeural", "voice_name": "云希·青年男声", "description": "清亮有活力，少年/青年男主"},
    {"voice_id": "zh-CN-YunjianNeural", "voice_name": "云健·沉稳男声", "description": "低沉浑厚，硬汉/将领/中年男"},
    {"voice_id": "zh-CN-YunyangNeural", "voice_name": "云扬·磁性男声", "description": "播音腔，旁白/长者/权威"},
    {"voice_id": "zh-CN-YunxiaNeural", "voice_name": "云夏·少年音", "description": "偏年轻、少年感"},
    {"voice_id": "zh-CN-XiaoxiaoNeural", "voice_name": "晓晓·温婉女声", "description": "标准温暖女声，百搭女主"},
    {"voice_id": "zh-CN-XiaoyiNeural", "voice_name": "晓伊·甜美女声", "description": "明亮活泼，少女"},
    {"voice_id": "zh-CN-liaoning-XiaobeiNeural", "voice_name": "晓北·东北女声", "description": "东北口音，市井/喜剧"},
    {"voice_id": "zh-CN-shaanxi-XiaoniNeural", "voice_name": "晓妮·陕西女声", "description": "陕西口音，地域角色"},
)

# 旧抽象预设 → 真实 edge 嗓音（迁移既有角色绑定 + TTS 兜底）
LEGACY_PRESET_TO_EDGE = {
    "preset_male_young": "zh-CN-YunxiNeural",
    "preset_male_steady": "zh-CN-YunjianNeural",
    "preset_female_sweet": "zh-CN-XiaoyiNeural",
    "preset_female_mature": "zh-CN-XiaoxiaoNeural",
    "preset_old_male": "zh-CN-YunyangNeural",
}


def seed_preset_voices(db: Session) -> None:
    """确保真实 edge 音色入库；清掉旧抽象预设并把绑定迁移到对应真实嗓音（幂等）。"""
    existing = {v.voice_id: v for v in db.scalars(select(AiVoice)).all()}
    changed = False
    for voice in EDGE_VOICES:
        if voice["voice_id"] not in existing:
            db.add(AiVoice(language="zh-CN", provider="edge", **voice))
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


def _preferred_voice_id(character: Character) -> str:
    traits = " ".join(
        value for value in (character.name, character.role, character.description, character.personality) if value
    )
    if any(keyword in traits for keyword in ("老人", "老年", "爷爷", "祖父", "长者", "将军", "统帅")):
        return "zh-CN-YunyangNeural"
    if any(keyword in traits for keyword in ("女性", "女孩", "少女", "姐姐", "母亲", "妻子", "女")):
        if any(keyword in traits for keyword in ("成熟", "沉稳", "母亲", "知性")):
            return "zh-CN-XiaoxiaoNeural"
        return "zh-CN-XiaoyiNeural"
    if any(keyword in traits for keyword in ("沉稳", "严肃", "威严", "内敛", "中年", "硬汉")):
        return "zh-CN-YunjianNeural"
    return "zh-CN-YunxiNeural"


def _fallback_assignments(characters: list[Character], voices: list[AiVoice]) -> dict[int, AiVoice]:
    by_id = {voice.voice_id: voice for voice in voices}
    assignments: dict[int, AiVoice] = {}
    for index, character in enumerate(characters):
        voice = by_id.get(_preferred_voice_id(character)) or voices[index % len(voices)]
        assignments[character.id] = voice
    return assignments


def _llm_assignments(
    db: Session,
    characters: list[Character],
    voices: list[AiVoice],
) -> dict[int, AiVoice]:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("voice_assigner")
    payload = {
        "characters": [
            {
                "id": character.id,
                "name": character.name,
                "role": character.role,
                "description": character.description,
                "personality": character.personality,
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
    }
    messages = [
        {
            "role": "system",
            "content": skill + "\n\n只输出 JSON：{\"assignments\":[{\"character_id\":1,\"voice_id\":\"...\"}]}。每个角色只能使用给定音色。",
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    raw = chat(messages, base_url, api_key, model, temperature=0.2, response_format={"type": "json_object"})
    data = _parse_json(raw)
    voice_by_id = {voice.voice_id: voice for voice in voices}
    character_ids = {character.id for character in characters}
    assignments: dict[int, AiVoice] = {}
    for item in data.get("assignments") or []:
        character_id = item.get("character_id")
        voice = voice_by_id.get(item.get("voice_id"))
        if character_id in character_ids and voice is not None:
            assignments[character_id] = voice
    return assignments


def assign_character_voices(db: Session, drama_id: int) -> list[dict]:
    characters = db.scalars(
        select(Character)
        .where(
            Character.drama_id == drama_id,
            Character.deleted_at.is_(None),
            (Character.voice_style.is_(None) | (Character.voice_style == "")),
        )
        .order_by(Character.id)
    ).all()
    if not characters:
        return []
    voices = db.scalars(select(AiVoice).order_by(AiVoice.id)).all()
    if not voices:
        raise LookupError("没有可用音色")

    fallback = _fallback_assignments(characters, voices)
    try:
        assignments = _llm_assignments(db, characters, voices)
    except LLMNotConfigured:
        assignments = {}

    results: list[dict] = []
    for character in characters:
        voice = assignments.get(character.id) or fallback[character.id]
        character.voice_style = voice.voice_id
        character.voice_provider = voice.provider
        results.append(
            {
                "character_id": character.id,
                "character_name": character.name,
                "voice_id": voice.voice_id,
                "voice_name": voice.voice_name,
                "voice_provider": voice.provider,
            }
        )
    db.commit()
    return results
