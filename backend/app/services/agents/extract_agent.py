"""提取 Agent —— 用 xg_cast_extract skill 从剧本/原文抽取 角色 / 场景 / 道具（JSON），按名去重入库。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, Drama, Episode, EpisodeCharacter, EpisodeScene, Prop, Scene
from app.services.agents.script_agent import load_skill
from app.services.asset_generation import scrub_people_from_prompt
from app.services.llm.client import chat_text, resolve_llm

_INSTRUCTION = """
阅读下面的剧本/小说内容，提取文本中真实出现的【角色】【场景】【道具】。
场景 prompt 必须是纯环境空镜描述（建筑/陈设/光线/天气），严禁写入任何人物姓名、士兵、站位或动作。
严格输出一个 JSON 对象（不要解释，不要 Markdown 代码块），结构如下：
{
  "characters": [{"name":"中文角色名","role":"主角/配角/龙套","appearance":"中文外貌描述","personality":"中文性格标签","description":"中文背景和人物关系"}],
  "scenes": [{"location":"中文地点","time":"中文时间","atmosphere":"中文气氛","prompt":"中文场景出图提示词，纯背景，不要人物"}],
  "props": [{"name":"中文道具名","type":"中文类型","description":"中文描述","prompt":"中文道具出图提示词"}]
}
只提取内容中实际出现的项目；没有则用空数组。必须是合法 JSON。所有 prompt 必须使用中文，不要英文。
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


def extract(
    db: Session,
    content: str,
    temperature: float = 0.3,
    *,
    drama_id: int | None = None,
) -> dict:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("xg_cast_extract")
    style_note = ""
    try:
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_CAST

        drama = db.get(Drama, drama_id) if drama_id is not None else None
        contract = compose(db=db, drama=drama, stage=STAGE_CAST)
        style_note = "\n\n" + contract.system_prefix()
    except Exception:  # noqa: BLE001
        style_note = ""
    messages = [
        {"role": "system", "content": skill + "\n\n" + _INSTRUCTION + style_note},
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
            appearance = (c.get("appearance") or "").strip()
            row = Character(
                drama_id=drama_id,
                name=name,
                role=c.get("role"),
                appearance=c.get("appearance"),
                personality=c.get("personality"),
                description=c.get("description"),
                # 预填可编辑提示词，减少角色页空白
                image_prompt=appearance or None,
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
        # 入库前强制清人物诱导词，避免场景页带着脏 prompt
        raw_prompt = (s.get("prompt") or "").strip()
        cleaned = scrub_people_from_prompt(raw_prompt)
        if not cleaned or len(cleaned) < 6:
            atm = (s.get("atmosphere") or "").strip()
            cleaned = "，".join(x for x in (location, time, atm) if x)
        row = db.scalars(
            select(Scene).where(Scene.drama_id == drama_id, Scene.location == location, Scene.time == time)
        ).first()
        if row is None:
            row = Scene(
                drama_id=drama_id,
                episode_id=episode_id,
                location=location,
                time=time,
                prompt=cleaned,
                status="pending",
            )
            db.add(row)
            db.flush()
            new["scenes"] += 1
        else:
            # 已有行若 prompt 很脏，补一次清洗
            if row.prompt and scrub_people_from_prompt(row.prompt) != row.prompt:
                row.prompt = scrub_people_from_prompt(row.prompt) or cleaned
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
