"""小白快速云出片：一键写入通义万相（出图）+ 豆包 Seedance（出视频）节点。

仅使用公开 HTTP API 协议自研适配，不捆绑第三方源码。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import ComputeNode as ComputeNodeRow
from app.services.compute.cloud_api import CloudApiNode, PROVIDERS
from app.services.compute.registry import describe_compute_readiness


WAN_NAME = "西瓜云·通义出图"
SEEDANCE_NAME = "西瓜云·豆包出片"


def _upsert_cloud_node(
    db: Session,
    *,
    name: str,
    provider: str,
    api_key: str,
    model: str | None,
    priority: int,
    capabilities: list[str],
) -> ComputeNodeRow:
    adapter = PROVIDERS.get(provider)
    base = adapter.default_base_url if adapter else ""
    model_name = (model or "").strip() or (adapter.default_model if adapter else "")
    row = db.scalars(
        select(ComputeNodeRow).where(
            ComputeNodeRow.type == "cloud_api",
            ComputeNodeRow.provider == provider,
            ComputeNodeRow.name == name,
        )
    ).first()
    caps = json.dumps(capabilities, ensure_ascii=False)
    if row is None:
        row = ComputeNodeRow(
            name=name,
            type="cloud_api",
            base_url=base,
            provider=provider,
            api_key=api_key.strip(),
            model=model_name,
            priority=priority,
            is_active=True,
            capabilities=caps,
        )
        db.add(row)
    else:
        row.base_url = base or row.base_url
        if api_key.strip():
            row.api_key = api_key.strip()
        if model_name:
            row.model = model_name
        row.priority = priority
        row.is_active = True
        row.capabilities = caps
    return row


async def quick_setup_cloud(
    db: Session,
    *,
    wan_api_key: str | None = None,
    seedance_api_key: str | None = None,
    wan_model: str | None = None,
    seedance_model: str | None = None,
    deactivate_comfy: bool = False,
    test_connection: bool = True,
) -> dict[str, Any]:
    """保存云 Key 并可选连通性测试。至少提供一种 Key。"""
    wan_key = (wan_api_key or "").strip()
    seed_key = (seedance_api_key or "").strip()
    if not wan_key and not seed_key:
        raise ValueError("请至少填写通义万相 Key（出图）或豆包 Seedance Key（出视频）")

    created: list[dict] = []
    if wan_key:
        row = _upsert_cloud_node(
            db,
            name=WAN_NAME,
            provider="wan",
            api_key=wan_key,
            model=wan_model,
            priority=120,
            capabilities=["image", "t2i"],
        )
        created.append({"id": row.id, "name": row.name, "provider": "wan", "role": "image"})
    if seed_key:
        row = _upsert_cloud_node(
            db,
            name=SEEDANCE_NAME,
            provider="seedance",
            api_key=seed_key,
            model=seedance_model,
            priority=110,
            capabilities=["video"],
        )
        created.append({"id": row.id, "name": row.name, "provider": "seedance", "role": "video"})

    if deactivate_comfy:
        for row in db.scalars(
            select(ComputeNodeRow).where(ComputeNodeRow.type.in_(["local_comfy", "remote_comfy"]))
        ).all():
            row.is_active = False

    db.commit()

    tests: list[dict] = []
    if test_connection:
        for item in created:
            row = db.get(ComputeNodeRow, item["id"])
            if row is None:
                continue
            node = CloudApiNode(row.provider or "", row.base_url, row.api_key, row.model, row.token)
            ok = await node.health()
            tests.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "provider": row.provider,
                    "ok": ok,
                    "error": None if ok else (node.last_error or "检测失败"),
                }
            )
            row.last_status = "online" if ok else "offline"
        db.commit()

    readiness = describe_compute_readiness(db)
    all_ok = all(t.get("ok") for t in tests) if tests else True
    return {
        "ok": all_ok and readiness.get("cloud_ready"),
        "nodes": created,
        "tests": tests,
        "readiness": readiness,
        "message": (
            "云出图+云出片已就绪，可以不启动 ComfyUI 直接一键出片"
            if readiness.get("cloud_ready")
            else "已保存部分云节点。完整免本地显卡预览需要同时配置出图 Key 与出视频 Key"
        ),
    }


def cloud_status(db: Session) -> dict[str, Any]:
    readiness = describe_compute_readiness(db)
    wan = db.scalars(
        select(ComputeNodeRow).where(
            ComputeNodeRow.type == "cloud_api",
            ComputeNodeRow.provider == "wan",
            ComputeNodeRow.is_active.is_(True),
        )
    ).first()
    seed = db.scalars(
        select(ComputeNodeRow).where(
            ComputeNodeRow.type == "cloud_api",
            ComputeNodeRow.provider == "seedance",
            ComputeNodeRow.is_active.is_(True),
        )
    ).first()
    return {
        "readiness": readiness,
        "wan_configured": bool(wan and wan.api_key),
        "seedance_configured": bool(seed and seed.api_key),
        "wan_model": wan.model if wan else None,
        "seedance_model": seed.model if seed else None,
    }
