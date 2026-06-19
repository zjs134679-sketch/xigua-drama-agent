"""算力节点注册/选择：DB compute_nodes 中取 active 且优先级最高者；缺省回退本地 ComfyUI。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import ComputeNode as ComputeNodeRow
from app.services.compute.base import ComputeNode
from app.services.compute.local_comfy import LocalComfyNode
from app.services.compute.remote_comfy import RemoteComfyNode


def build_node(row: ComputeNodeRow) -> ComputeNode:
    if row.type == "remote_comfy":
        return RemoteComfyNode(row.base_url, row.token)
    # cloud_api 待 M7 实现；目前未知类型按本地处理
    return LocalComfyNode(row.base_url, row.token)


def get_active_node(db: Session | None = None) -> ComputeNode:
    if db is not None:
        row = (
            db.scalars(
                select(ComputeNodeRow)
                .where(ComputeNodeRow.is_active.is_(True))
                .order_by(ComputeNodeRow.priority.desc())
            ).first()
        )
        if row is not None:
            return build_node(row)
    return LocalComfyNode(settings.comfyui_base_url)
