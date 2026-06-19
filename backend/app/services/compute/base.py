"""算力节点抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ImageJob:
    prompt: str
    negative: str | None = None
    width: int = 768
    height: int = 1024
    steps: int | None = None
    seed: int | None = None
    workflow: str = "flux-t2i.api.json"


@dataclass
class JobResult:
    status: str  # completed / failed
    image_path: str | None = None
    image_url: str | None = None
    error: str | None = None
    meta: dict = field(default_factory=dict)


class ComputeNode(ABC):
    """所有算力节点的统一接口。type ∈ local_comfy / remote_comfy / cloud_api。"""

    type: str = "base"

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        return {}

    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def text2image(self, job: ImageJob) -> JobResult: ...
