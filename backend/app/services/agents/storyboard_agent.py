"""分镜 Agent —— 用 storyboard_breaker skill 把剧本拆解为分镜清单。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Storyboard, StoryboardCharacter
from app.services.agents.script_agent import load_skill
from app.services.llm.client import chat_text, resolve_llm
from app.services.storyboard_references import sync_storyboard_characters

_OUTPUT_SPEC = (
    "\n\n把用户剧本拆成分镜清单。所有提示词字段必须使用中文，不要输出英文提示词。**只输出 JSON**，格式："
    '{"storyboards":[{'
    '"storyboard_number":1,'
    '"title":"3到5个字的中文标题",'
    '"location":"中文地点",'
    '"time":"中文时间和光线",'
    '"shot_type":"中文景别（远景/全景/中景/近景/特写/大特写）",'
    '"angle":"中文机位角度",'
    '"movement":"中文镜头运动",'
    '"action":"谁做什么+表情动作，中文",'
    '"dialogue":"中文台词",'
    '"result":"中文画面结果",'
    '"atmosphere":"中文光线/色调/气氛",'
    '"image_prompt":"中文静态画面提示词，纯视觉描述，包含景别、构图、人物动作、环境、光线、气氛，不要真实人名",'
    '"video_prompt":"中文视频提示词，按3秒分段描述动作推进和镜头变化",'
    '"bgm_prompt":"中文配乐风格",'
    '"sound_effect":"中文关键音效",'
    '"duration":12'
    "}]}. duration 是 10 到 15 的整数。不要解释，不要前后缀。"
)


def break_storyboards(db: Session, script_content: str, temperature: float = 0.4) -> list[dict]:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("storyboard_breaker")
    messages = [
        {"role": "system", "content": skill + _OUTPUT_SPEC},
        {"role": "user", "content": script_content},
    ]
    raw = chat_text(
        messages,
        base_url,
        api_key,
        model,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    shots = data.get("storyboards") or data.get("shots") or []
    if not isinstance(shots, list):
        raise ValueError("分镜返回格式不是列表")
    return shots


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def save_storyboards(db: Session, episode_id: int, shots: list[dict]) -> int:
    """整集重建分镜：清掉旧的，按新清单插入。返回新建数量。"""
    old = db.scalars(
        select(Storyboard).where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
    ).all()
    old_ids = [row.id for row in old]
    if old_ids:
        links = db.scalars(select(StoryboardCharacter).where(StoryboardCharacter.storyboard_id.in_(old_ids))).all()
        for link in links:
            db.delete(link)
    for row in old:
        db.delete(row)

    count = 0
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            continue
        row = Storyboard(
                episode_id=episode_id,
                storyboard_number=_int(shot.get("storyboard_number"), index),
                title=shot.get("title"),
                location=shot.get("location"),
                time=shot.get("time"),
                shot_type=shot.get("shot_type"),
                angle=shot.get("angle"),
                movement=shot.get("movement"),
                action=shot.get("action"),
                result=shot.get("result"),
                atmosphere=shot.get("atmosphere"),
                image_prompt=shot.get("image_prompt"),
                video_prompt=shot.get("video_prompt"),
                bgm_prompt=shot.get("bgm_prompt"),
                sound_effect=shot.get("sound_effect"),
                dialogue=shot.get("dialogue"),
                duration=_int(shot.get("duration"), 12),
                status="pending",
            )
        db.add(row)
        db.flush()
        sync_storyboard_characters(db, row)
        count += 1
    db.commit()
    return count


def polish_prompts(db: Session, shots: list[dict], temperature: float = 0.4) -> list[dict]:
    """用 LLM 为现有分镜生成中文画面提示词。"""
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("storyboard_breaker")
    prompt = (
        "下面是分镜数据（JSON 数组）。请为每个分镜生成高质量中文静态画面提示词 image_prompt。"
        "提示词要包含景别、机位、构图、人物动作、表情、环境、光线和气氛，可直接用于 AI 出图。"
        "必须输出中文，不要英文，不要中英混杂。"
        "\n\n**只输出 JSON**，格式：{\"prompts\":[{\"number\":镜头编号,\"prompt\":\"中文画面提示词\"}]}。不要解释。"
        f"\n\n分镜数据：\n{json.dumps(shots, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": skill},
        {"role": "user", "content": prompt},
    ]
    raw = chat_text(
        messages,
        base_url,
        api_key,
        model,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    prompts = data.get("prompts") or []
    if not isinstance(prompts, list):
        raise ValueError("润色提示词结果不是列表")
    return prompts
