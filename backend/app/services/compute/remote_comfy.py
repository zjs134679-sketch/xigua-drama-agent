"""国内远程主机 ComfyUI 适配器 —— 协议与本地一致，仅 baseURL + token 鉴权不同。

适配 智星云 / AutoDL / 恒源云 / 矩池云 / 趋动云 / PPIO 等：填公网映射端口 + token。
"""
from __future__ import annotations

from app.services.compute.local_comfy import LocalComfyNode


class RemoteComfyNode(LocalComfyNode):
    type = "remote_comfy"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}
