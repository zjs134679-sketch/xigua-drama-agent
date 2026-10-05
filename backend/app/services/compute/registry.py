"""算力节点注册/选择：按能力（出图/出片）挑选；缺省回退本地 ComfyUI。"""
from __future__ import annotations

import json
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import ComputeNode as ComputeNodeRow
from app.services.compute.base import ComputeNode
from app.services.compute.cloud_api import PROVIDERS, CloudApiNode
from app.services.compute.local_comfy import LocalComfyNode
from app.services.compute.remote_comfy import RemoteComfyNode

Capability = Literal["image", "video"]


def _model_settings(row: ComputeNodeRow) -> dict:
    if not row.extra:
        return {}
    try:
        data = json.loads(row.extra)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    settings_obj = data.get("model_settings")
    return settings_obj if isinstance(settings_obj, dict) else {}


def build_node(row: ComputeNodeRow) -> ComputeNode:
    if row.type == "remote_comfy":
        return RemoteComfyNode(row.base_url, row.token, _model_settings(row))
    if row.type == "cloud_api":
        return CloudApiNode(row.provider or "", row.base_url, row.api_key, row.model, row.token)
    return LocalComfyNode(row.base_url, row.token, _model_settings(row))


def _parse_caps(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    text = str(raw).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return {str(x).lower() for x in data}
        if isinstance(data, dict):
            return {str(k).lower() for k, v in data.items() if v}
    except (TypeError, json.JSONDecodeError):
        pass
    return {p.strip().lower() for p in text.replace("|", ",").split(",") if p.strip()}


def node_supports(row: ComputeNodeRow, capability: Capability) -> bool:
    """判断节点是否适合出图 / 出视频。"""
    caps = _parse_caps(row.capabilities)
    if caps:
        if capability in caps:
            return True
        # 写了 capabilities 但未包含目标能力
        if capability == "image" and ("t2i" in caps or "i2i" in caps):
            return True
        return False

    ntype = (row.type or "").strip().lower()
    if ntype in {"local_comfy", "remote_comfy"}:
        return True
    if ntype != "cloud_api":
        return True

    provider = (row.provider or "").strip().lower()
    adapter = PROVIDERS.get(provider)
    if adapter is None:
        return False
    if capability == "image":
        return adapter.media_type == "image"
    if capability == "video":
        return adapter.media_type == "video"
    return False


def list_active_rows(db: Session) -> list[ComputeNodeRow]:
    return list(
        db.scalars(
            select(ComputeNodeRow)
            .where(ComputeNodeRow.is_active.is_(True))
            .order_by(ComputeNodeRow.priority.desc(), ComputeNodeRow.id.desc())
        ).all()
    )


def get_active_node(db: Session | None = None, capability: Capability | None = None) -> ComputeNode:
    """产品默认：出图/出片优先本地或远程 ComfyUI；云 API 仅作备用（若有人配置）。

    剧本/分镜提示词走 LLM API，与算力节点无关。
    """
    if db is not None:
        rows = list_active_rows(db)
        if capability:
            # 1) Comfy 优先
            for row in rows:
                if row.type in {"local_comfy", "remote_comfy"} and node_supports(row, capability):
                    return build_node(row)
            # 2) 其它可用节点（含历史云配置）
            for row in rows:
                if node_supports(row, capability):
                    return build_node(row)
        # 无能力要求时也优先 Comfy
        for row in rows:
            if row.type in {"local_comfy", "remote_comfy"}:
                return build_node(row)
        if rows:
            return build_node(rows[0])
    return LocalComfyNode(settings.comfyui_base_url)


def get_node(
    db: Session,
    node_id: int | None,
    capability: Capability | None = None,
) -> ComputeNode:
    """显式 node_id 优先；否则按能力选活跃节点。"""
    if node_id is not None:
        row = db.get(ComputeNodeRow, node_id)
        if row is not None and row.is_active:
            if capability is None or node_supports(row, capability):
                return build_node(row)
            # 指定节点不支持该能力时，回落能力匹配
    return get_active_node(db, capability=capability)


def describe_compute_readiness(db: Session) -> dict:
    """小白环境检查：是否具备出图/出片路径。"""
    rows = list_active_rows(db)
    image_nodes: list[dict] = []
    video_nodes: list[dict] = []
    for row in rows:
        item = {
            "id": row.id,
            "name": row.name,
            "type": row.type,
            "provider": row.provider,
        }
        if node_supports(row, "image"):
            image_nodes.append(item)
        if node_supports(row, "video"):
            video_nodes.append(item)
    # 无激活节点时，默认认为会回落本机 Comfy（可能离线）
    has_image = bool(image_nodes) or not rows
    has_video = bool(video_nodes) or not rows
    mode = "none"
    if any(r.type in {"local_comfy", "remote_comfy"} for r in rows):
        mode = "comfy"
    cloud_img = any(r.type == "cloud_api" and node_supports(r, "image") for r in rows)
    cloud_vid = any(r.type == "cloud_api" and node_supports(r, "video") for r in rows)
    if cloud_img or cloud_vid:
        mode = "cloud" if mode == "none" else "hybrid"
    if not rows:
        mode = "comfy_default"
    return {
        "mode": mode,
        "has_image_path": has_image,
        "has_video_path": has_video,
        "image_nodes": image_nodes,
        "video_nodes": video_nodes,
        "active_count": len(rows),
        "cloud_ready": cloud_img and cloud_vid,
        "hint": (
            "云出图+云出片已就绪，可不启动 ComfyUI"
            if cloud_img and cloud_vid
            else "可在设置里填通义 Key（出图）+ 豆包 Key（出视频），无本地显卡也能预览"
            if not rows or mode == "comfy_default"
            else "请保证至少有一个出图节点、一个出片节点（或本地 Comfy 同时支持两者）"
        ),
    }
