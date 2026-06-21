from __future__ import annotations

from pydantic import BaseModel


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


class StoryboardPromptUpdate(BaseModel):
    image_prompt: str


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
