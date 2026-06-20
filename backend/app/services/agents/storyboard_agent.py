"""分镜 Agent —— 用 storyboard_breaker skill 把剧本拆解为分镜清单。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Storyboard
from app.services.agents.script_agent import load_skill
from app.services.llm.client import chat, resolve_llm

_OUTPUT_SPEC = (
    "\n\n请把用户给出的剧本拆解为分镜清单，**只输出 JSON**，格式："
    '{"storyboards":[{'
    '"storyboard_number":1,'
    '"title":"3-5字标题",'
    '"location":"地点",'
    '"time":"时间+光线",'
    '"shot_type":"景别(远景/全景/中景/近景/特写)",'
    '"angle":"角度",'
    '"movement":"运镜",'
    '"action":"谁+怎么做+表情",'
    '"dialogue":"该镜头对白",'
    '"result":"画面结果",'
    '"atmosphere":"光线/色调/氛围",'
    '"image_prompt":"用于出图的英文静态画面提示词(纯画面,不含真实人名)",'
    '"video_prompt":"按3秒分段的视频提示词",'
    '"bgm_prompt":"配乐风格",'
    '"sound_effect":"关键音效",'
    '"duration":12'
    "}]}。duration 取 10-15 的整数。不要任何解释或前后缀。"
)


def break_storyboards(db: Session, script_content: str, temperature: float = 0.4) -> list[dict]:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("storyboard_breaker")
    messages = [
        {"role": "system", "content": skill + _OUTPUT_SPEC},
        {"role": "user", "content": script_content},
    ]
    raw = chat(
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
    for row in old:
        db.delete(row)

    count = 0
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            continue
        db.add(
            Storyboard(
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
        )
        count += 1
    db.commit()
    return count
