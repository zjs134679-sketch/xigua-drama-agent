from __future__ import annotations

from pydantic import BaseModel


class Text2ImageRequest(BaseModel):
    prompt: str
    negative: str | None = None
    width: int = 768
    height: int = 1024
    steps: int | None = None
    seed: int | None = None
    workflow: str = "flux-t2i.api.json"
    username: str | None = None  # 临时：auth 接入前用于本地三振计数


class ComputeNodeCreate(BaseModel):
    name: str
    type: str
    base_url: str
    token: str | None = None
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    priority: int = 100
    is_active: bool = True
    capabilities: str | None = None
    model_settings: dict | None = None
    adapter_code: str | None = None
    adapter_filename: str | None = None


class ComputeNodeUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    base_url: str | None = None
    token: str | None = None
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    priority: int | None = None
    is_active: bool | None = None
    capabilities: str | None = None
    model_settings: dict | None = None
    adapter_code: str | None = None
    adapter_filename: str | None = None
