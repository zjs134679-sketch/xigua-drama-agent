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
