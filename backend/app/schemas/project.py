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


class NovelImportRequest(BaseModel):
    text: str                     # 整篇小说原文
    max_chars: int = 2000         # 无章节标题时按长度兜底
    username: str | None = None


class ScriptGenerateRequest(BaseModel):
    episode_id: int
    temperature: float = 0.7
    username: str | None = None


class ScriptDraftRequest(BaseModel):
    content: str  # 小说原文（快速试写，不落库）
    temperature: float = 0.7
    username: str | None = None


class ExtractRequest(BaseModel):
    episode_id: int
    username: str | None = None
