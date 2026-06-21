from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AiVoice, Character
from app.services.agents.script_agent import load_skill
from app.services.llm.client import LLMNotConfigured, chat, resolve_llm


PRESET_VOICES = (
    {"voice_id": "preset_male_young", "voice_name": "青年男声", "description": "清晰、有活力", "language": "zh-CN", "provider": "preset"},
    {"voice_id": "preset_male_steady", "voice_name": "沉稳男声", "description": "低沉、稳重", "language": "zh-CN", "provider": "preset"},
    {"voice_id": "preset_female_sweet", "voice_name": "甜美女声", "description": "明亮、柔和", "language": "zh-CN", "provider": "preset"},
    {"voice_id": "preset_female_mature", "voice_name": "成熟女声", "description": "从容、知性", "language": "zh-CN", "provider": "preset"},
    {"voice_id": "preset_old_male", "voice_name": "老年男声", "description": "厚重、沧桑", "language": "zh-CN", "provider": "preset"},
)


def seed_preset_voices(db: Session) -> None:
    """Seed binding metadata only; this does not synthesize or claim playable audio."""
    if db.scalars(select(AiVoice.id).limit(1)).first() is not None:
        return
    db.add_all(AiVoice(**voice) for voice in PRESET_VOICES)
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
    if any(keyword in traits for keyword in ("老人", "老年", "爷爷", "祖父")):
        return "preset_old_male"
    if any(keyword in traits for keyword in ("女性", "女孩", "少女", "姐姐", "母亲", "妻子")):
        if any(keyword in traits for keyword in ("成熟", "沉稳", "母亲")):
            return "preset_female_mature"
        return "preset_female_sweet"
    if any(keyword in traits for keyword in ("沉稳", "严肃", "威严", "内敛", "中年")):
        return "preset_male_steady"
    return "preset_male_young"


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
