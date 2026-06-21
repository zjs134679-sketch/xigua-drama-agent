"""提取 Agent —— 用 extractor skill 从剧本/原文抽取 角色 / 场景 / 道具（JSON），按名去重入库。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, EpisodeCharacter, EpisodeScene, Prop, Scene
from app.services.agents.script_agent import load_skill
from app.services.llm.client import chat_text, resolve_llm

_INSTRUCTION = """
Read the script/novel content below and extract actual [characters] [scenes] [props] that appear in the text.
Output strictly a single JSON object (no explanation, no markdown code blocks), with the following structure:
{
  "characters": [{"name":"character name in Chinese","role":"protagonist/supporting/extra","appearance":"appearance description in Chinese","personality":"personality tags in Chinese","description":"background and relationships in Chinese"}],
  "scenes": [{"location":"location in Chinese","time":"time of day in Chinese","atmosphere":"atmosphere in Chinese","prompt":"English background image prompt (pure background, no people)"}],
  "props": [{"name":"prop name in Chinese","type":"type","description":"description in Chinese","prompt":"English image prompt"}]
}
Only extract what actually appears in the content; use empty arrays if none found. Must be valid JSON.
"""


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        nl = raw.find("\n")
        if nl != -1 and raw[:nl].strip().lower() in ("json", ""):
            raw = raw[nl + 1 :]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e != -1:
            return json.loads(raw[s : e + 1])
        raise


def extract(db: Session, content: str, temperature: float = 0.3) -> dict:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("extractor")
    messages = [
        {"role": "system", "content": skill + "\n\n" + _INSTRUCTION},
        {"role": "user", "content": content},
    ]
    raw = chat_text(messages, base_url, api_key, model, temperature=temperature, response_format={"type": "json_object"})
    data = _parse_json(raw)
    return {
        "characters": data.get("characters") or [],
        "scenes": data.get("scenes") or [],
        "props": data.get("props") or [],
    }


def save_extracted(db: Session, drama_id: int, episode_id: int | None, extracted: dict) -> dict:
    new = {"characters": 0, "scenes": 0, "props": 0}

    for c in extracted["characters"]:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        row = db.scalars(select(Character).where(Character.drama_id == drama_id, Character.name == name)).first()
        if row is None:
            row = Character(
                drama_id=drama_id,
                name=name,
                role=c.get("role"),
                appearance=c.get("appearance"),
                personality=c.get("personality"),
                description=c.get("description"),
            )
            db.add(row)
            db.flush()
            new["characters"] += 1
        if episode_id:
            linked = db.scalars(
                select(EpisodeCharacter).where(
                    EpisodeCharacter.episode_id == episode_id, EpisodeCharacter.character_id == row.id
                )
            ).first()
            if not linked:
                db.add(EpisodeCharacter(episode_id=episode_id, character_id=row.id))

    for s in extracted["scenes"]:
        location = (s.get("location") or "").strip()
        time = (s.get("time") or "").strip() or "未知"
        if not location:
            continue
        row = db.scalars(
            select(Scene).where(Scene.drama_id == drama_id, Scene.location == location, Scene.time == time)
        ).first()
        if row is None:
            row = Scene(
                drama_id=drama_id,
                episode_id=episode_id,
                location=location,
                time=time,
                prompt=s.get("prompt") or "",
                status="pending",
            )
            db.add(row)
            db.flush()
            new["scenes"] += 1
        if episode_id:
            linked = db.scalars(
                select(EpisodeScene).where(EpisodeScene.episode_id == episode_id, EpisodeScene.scene_id == row.id)
            ).first()
            if not linked:
                db.add(EpisodeScene(episode_id=episode_id, scene_id=row.id))

    for p in extracted["props"]:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        row = db.scalars(select(Prop).where(Prop.drama_id == drama_id, Prop.name == name)).first()
        if row is None:
            db.add(
                Prop(
                    drama_id=drama_id,
                    name=name,
                    type=p.get("type"),
                    description=p.get("description"),
                    prompt=p.get("prompt"),
                )
            )
            new["props"] += 1

    db.commit()
    return new
