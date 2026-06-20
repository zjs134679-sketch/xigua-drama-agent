from __future__ import annotations

from pydantic import BaseModel


class LLMConfigIn(BaseModel):
    provider: str | None = None          # deepseek / qwen / doubao / kimi / zhipu …
    base_url: str
    model: str | None = None
    api_key: str | None = None           # 省略或留空表示沿用已存 key（不覆盖）


class LLMConfigOut(BaseModel):
    configured: bool
    source: str                          # db / env / none
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key_configured: bool = False
    api_key_masked: str | None = None


class LLMTestIn(BaseModel):
    # 测试时可临时传入一组配置；都省略则用已存配置
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class LLMTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    reply: str | None = None
