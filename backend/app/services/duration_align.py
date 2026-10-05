"""分镜时长与视频提示词对齐（Comfy 单镜 2–5 秒）。"""
from __future__ import annotations

import re
from typing import Any

from app.services.prompt_orchestrate import align_video_prompt_to_duration

MIN_SEC = 3
MAX_SEC = 5
DEFAULT_SEC = 5


def clamp_shot_duration(value: Any, default: int = DEFAULT_SEC) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = default
    if raw <= 0:
        raw = default
    return max(MIN_SEC, min(raw, MAX_SEC))


def align_shot_dict(shot: dict, *, default_duration: int = DEFAULT_SEC) -> dict:
    """就地规范 duration + video_prompt 时间片。"""
    if not isinstance(shot, dict):
        return shot
    dur = clamp_shot_duration(shot.get("duration"), default_duration)
    shot["duration"] = dur
    vp = shot.get("video_prompt")
    if isinstance(vp, str) and vp.strip():
        shot["video_prompt"] = align_video_prompt_to_duration(vp, dur)
    # image_prompt 去掉「0-5秒」类时间片（静帧不需要）
    ip = shot.get("image_prompt")
    if isinstance(ip, str) and ip.strip():
        cleaned = re.sub(
            r"\d+(?:\.\d+)?\s*[-–—~～到至]\s*\d+(?:\.\d+)?\s*(?:秒|s|S)\s*[：:.]?\s*",
            "",
            ip,
        )
        shot["image_prompt"] = re.sub(r"\s{2,}", " ", cleaned).strip("，, ") or ip
    return shot


def align_shots(shots: list[dict]) -> list[dict]:
    return [align_shot_dict(s) if isinstance(s, dict) else s for s in shots]
