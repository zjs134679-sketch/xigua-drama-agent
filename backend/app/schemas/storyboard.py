from __future__ import annotations

from pydantic import BaseModel, Field


class StoryboardGenerateRequest(BaseModel):
    episode_id: int
    temperature: float = 0.4
    username: str | None = None


class StoryboardImageRequest(BaseModel):
    prompt: str | None = None          # 用户编辑的完整画面提示词（留空用已存）
    art_style_id: int | None = None
    username: str | None = None
    node_id: int | None = None
    resolution: str | None = None
    extra: str | None = None


class StoryboardUpdate(BaseModel):
    title: str | None = None
    location: str | None = None
    time: str | None = None
    shot_type: str | None = None
    angle: str | None = None
    movement: str | None = None
    action: str | None = None
    result: str | None = None
    atmosphere: str | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    bgm_prompt: str | None = None
    sound_effect: str | None = None
    dialogue: str | None = None
    description: str | None = None
    # 与本地 ComfyUI 单镜视频上限一致（最长 5 秒）
    duration: int | None = Field(default=None, ge=1, le=5)
    speaking_character_id: int | None = None
    reference_images: list[str] | None = None


class BatchDeleteRequest(BaseModel):
    ids: list[int]


class BatchGenerateImageRequest(BaseModel):
    ids: list[int]
    art_style_id: int | None = None
    username: str | None = None
    node_id: int | None = None
    resolution: str | None = None


class BatchGeneratePromptRequest(BaseModel):
    ids: list[int]
    temperature: float = 0.4
    username: str | None = None


class StoryboardSplitRequest(BaseModel):
    """长镜拆解：把一镜拆成连续 3–5 秒子镜。"""
    parts: int | None = Field(default=None, ge=2, le=4)
    use_llm: bool = True
    username: str | None = None
