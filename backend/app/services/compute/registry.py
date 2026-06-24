"""算力节点注册/选择：DB compute_nodes 中取 active 且优先级最高者；缺省回退本地 ComfyUI。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import ComputeNode as ComputeNodeRow
from app.services.compute.base import ComputeNode
from app.services.compute.cloud_api import CloudApiNode
from app.services.compute.local_comfy import LocalComfyNode
from app.services.compute.remote_comfy import RemoteComfyNode


def _model_settings(row: ComputeNodeRow) -> dict:
    if not row.extra:
        return {}
    try:
        data = json.loads(row.extra)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    settings = data.get("model_settings")
    return settings if isinstance(settings, dict) else {}


def build_node(row: ComputeNodeRow) -> ComputeNode:
    if row.type == "remote_comfy":
        return RemoteComfyNode(row.base_url, row.token, _model_settings(row))
    if row.type == "cloud_api":
        return CloudApiNode(row.provider or "", row.base_url, row.api_key, row.model, row.token)
    return LocalComfyNode(row.base_url, row.token, _model_settings(row))


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


def get_node(db: Session, node_id: int | None) -> ComputeNode:
    """Return an explicitly selected active node, or use the normal active-node fallback."""
    if node_id is not None:
        row = db.get(ComputeNodeRow, node_id)
        if row is not None and row.is_active:
            return build_node(row)
    return get_active_node(db)
