"""分镜 Agent —— 用 storyboard_breaker skill 把剧本拆解为分镜清单。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Storyboard
from app.services.agents.script_agent import load_skill
from app.services.llm.client import chat_text, resolve_llm

_OUTPUT_SPEC = (
    "\n\nBreak the user's script into a shot list. **Output JSON only**, format:"
    '{"storyboards":[{'
    '"storyboard_number":1,'
    '"title":"3-5 character Chinese title",'
    '"location":"location in Chinese",'
    '"time":"time + lighting in Chinese",'
    '"shot_type":"shot size (extreme long shot/long shot/medium shot/close-up/extreme close-up)",'
    '"angle":"camera angle",'
    '"movement":"camera movement",'
    '"action":"who does what + expression in Chinese",'
    '"dialogue":"dialogue in Chinese",'
    '"result":"visual result",'
    '"atmosphere":"lighting/color tone/mood",'
    '"image_prompt":"English static image prompt for AI image generation (pure visual description, no real person names)",'
    '"video_prompt":"video prompt segmented by 3-second intervals",'
    '"bgm_prompt":"background music style",'
    '"sound_effect":"key sound effects",'
    '"duration":12'
    "}]}. duration should be an integer between 10-15. No explanation or prefix/suffix."
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


def polish_prompts(db: Session, shots: list[dict], temperature: float = 0.4) -> list[dict]:
    """Use LLM to generate polished English image prompts for existing shot list."""
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("storyboard_breaker")
    prompt = (
        "Below is shot data (JSON array). Generate a high-quality English image prompt (image_prompt) for each shot. "
        "The prompt should include shot size, angle, camera movement, character action, expression, lighting and atmosphere, "
        "suitable for direct use in AI image generation."
        "\n\n**Output JSON only**, format: {\"prompts\":[{\"number\":shot_number,\"prompt\":\"image prompt\"}]}. No explanation."
        f"\n\nShot data:\n{json.dumps(shots, ensure_ascii=False)}"
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
        raise ValueError("Polished prompts result is not a list")
    return prompts
