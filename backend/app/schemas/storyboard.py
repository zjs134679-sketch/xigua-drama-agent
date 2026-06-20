from __future__ import annotations

from pydantic import BaseModel


class StoryboardGenerateRequest(BaseModel):
    episode_id: int
    temperature: float = 0.4
    username: str | None = None


class StoryboardImageRequest(BaseModel):
    art_style_id: int | None = None
    username: str | None = None
