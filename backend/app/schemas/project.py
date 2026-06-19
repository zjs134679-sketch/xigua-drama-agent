from __future__ import annotations

from pydantic import BaseModel


class DramaCreate(BaseModel):
    title: str
    description: str | None = None
    genre: str | None = None
    style: str | None = "realistic"


class EpisodeCreate(BaseModel):
    episode_number: int
    title: str
    content: str | None = None  # 小说原文


class ScriptGenerateRequest(BaseModel):
    episode_id: int
    temperature: float = 0.7


class ScriptDraftRequest(BaseModel):
    content: str  # 小说原文（快速试写，不落库）
    temperature: float = 0.7
