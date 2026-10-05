from __future__ import annotations

from pydantic import BaseModel


class StyleBibleIn(BaseModel):
    """项目风格圣经（西瓜原创）。"""

    art_style_id: int | None = None
    visual_pack: str | None = None
    visual_name: str | None = None
    narrative_tag: str | None = None
    pacing_profile: str | None = "pace_balanced"
    aspect: str | None = "9:16"
    version: int | None = 1


class DramaCreate(BaseModel):
    title: str
    description: str | None = None
    genre: str | None = None
    style: str | None = "realistic"
    # 风格圣经字段（可选；写入 style_bible JSON）
    art_style_id: int | None = None
    pacing_profile: str | None = None
    narrative_tag: str | None = None
    aspect: str | None = None


class EpisodeCreate(BaseModel):
    episode_number: int
    title: str
    content: str | None = None  # 小说原文


class NovelImportRequest(BaseModel):
    text: str                     # 整篇小说原文
    max_chars: int = 2000         # 无章节标题时按长度兜底
    username: str | None = None


class AssetPromptUpdate(BaseModel):
    prompt: str                   # 角色/场景的可编辑出图提示词


class VoiceBindingRequest(BaseModel):
    voice_id: str
    voice_provider: str


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
