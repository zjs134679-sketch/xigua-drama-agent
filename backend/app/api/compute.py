from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.system import ComputeNode as ComputeNodeRow
from app.schemas.compute import Text2ImageRequest
from app.services.compliance import check
from app.services.compliance.enforce import record_violation
from app.services.compute import ImageJob, get_active_node

router = APIRouter(prefix="/compute", tags=["compute"])


def _extra_payload(row: ComputeNodeRow) -> dict:
    if not row.extra:
        return {}
    try:
        data = json.loads(row.extra)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge_extra(
    current: str | None,
    *,
    model_settings: dict | None = None,
    adapter_code: str | None = None,
    adapter_filename: str | None = None,
) -> str | None:
    data: dict = {}
    if current:
        try:
            parsed = json.loads(current)
            if isinstance(parsed, dict):
                data.update(parsed)
        except (TypeError, json.JSONDecodeError):
            data = {}
    if model_settings is not None:
        data["model_settings"] = model_settings
    if adapter_code is not None:
        data["adapter_code"] = adapter_code
    if adapter_filename is not None:
        data["adapter_filename"] = adapter_filename
    return json.dumps(data, ensure_ascii=False) if data else None


def _node_payload(row: ComputeNodeRow) -> dict:
    extra = _extra_payload(row)
    adapter_code = extra.get("adapter_code")
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "base_url": row.base_url,
        "provider": row.provider,
        "model": row.model,
        "priority": row.priority,
        "is_active": row.is_active,
        "capabilities": row.capabilities,
        "last_status": row.last_status,
        "token_configured": bool(row.token),
        "api_key_configured": bool(row.api_key),
        "api_key_masked": "••••••••" if row.api_key else None,
        "model_settings": extra.get("model_settings") if isinstance(extra.get("model_settings"), dict) else {},
        "adapter_filename": extra.get("adapter_filename") if isinstance(extra.get("adapter_filename"), str) else None,
        "adapter_configured": isinstance(adapter_code, str) and bool(adapter_code.strip()),
        "adapter_code": adapter_code if isinstance(adapter_code, str) else "",
        "created_at": str(row.created_at) if row.created_at else None,
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }


@router.get("/health")
async def compute_health(db: Session = Depends(get_db)) -> dict:
    node = get_active_node(db)
    return {"type": node.type, "base_url": node.base_url, "online": await node.health()}


@router.get("/nodes")
def list_nodes(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ComputeNodeRow).order_by(ComputeNodeRow.priority.desc())).all()
    return [_node_payload(row) for row in rows]


@router.post("/text2image")
async def text2image(req: Text2ImageRequest, db: Session = Depends(get_db)):
    # 0. 封号校验
    ensure_active_user(db, req.username)

    # 1. 合规过滤（对最终绘画提示词）
    result = check(req.prompt)
    if result.blocked:
        enf = record_violation(db, req.username, result, "image_prompt")
        return JSONResponse(
            status_code=451,
            content={
                "blocked": True,
                "level": "red",
                "hits": [h.__dict__ for h in result.hits],
                "violation_count": enf["violation_count"],
                "banned": enf["banned"],
                "message": "内容触发红线，已拦截并记录",
            },
        )

    # 2. 送算力节点生成
    node = get_active_node(db)
    job = ImageJob(
        prompt=req.prompt,
        negative=req.negative,
        width=req.width,
        height=req.height,
        steps=req.steps,
        seed=req.seed,
        workflow=req.workflow,
    )
    res = await node.text2image(job)
    return JSONResponse(
        status_code=200 if res.status == "completed" else 502,
        content={
            "status": res.status,
            "image_url": res.image_url,
            "image_path": res.image_path,
            "error": res.error,
            "warn": result.warn,
            "warn_hits": [h.__dict__ for h in result.hits] if result.warn else [],
            "meta": res.meta,
        },
    )


# ── 算力节点 CRUD ────────────────────────────────────────────────────────────

from app.schemas.compute import ComputeNodeCreate, ComputeNodeUpdate  # noqa: E402
from app.services.compute.registry import build_node  # noqa: E402


@router.post('/nodes', tags=['nodes'])
def create_node(body: ComputeNodeCreate, db: Session = Depends(get_db)):
    row = ComputeNodeRow(
        name=body.name,
        type=body.type,
        base_url=body.base_url,
        token=body.token,
        provider=body.provider,
        api_key=body.api_key,
        model=body.model,
        priority=body.priority,
        is_active=body.is_active,
        capabilities=body.capabilities,
        extra=_merge_extra(
            None,
            model_settings=body.model_settings,
            adapter_code=body.adapter_code,
            adapter_filename=body.adapter_filename,
        ),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _node_payload(row)


@router.put('/nodes/{node_id}', tags=['nodes'])
def update_node(node_id: int, body: ComputeNodeUpdate, db: Session = Depends(get_db)):
    row = db.query(ComputeNodeRow).filter(ComputeNodeRow.id == node_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Node not found')
    data = body.model_dump(exclude_none=True)
    extra_fields = {
        "model_settings": data.pop("model_settings", None),
        "adapter_code": data.pop("adapter_code", None),
        "adapter_filename": data.pop("adapter_filename", None),
    }
    for field, value in data.items():
        setattr(row, field, value)
    if any(value is not None for value in extra_fields.values()):
        row.extra = _merge_extra(row.extra, **extra_fields)
    db.commit()
    db.refresh(row)
    return _node_payload(row)


@router.delete('/nodes/{node_id}', tags=['nodes'])
def delete_node(node_id: int, db: Session = Depends(get_db)):
    row = db.query(ComputeNodeRow).filter(ComputeNodeRow.id == node_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Node not found')
    db.delete(row)
    db.commit()
    return {'deleted': True}


@router.post('/nodes/{node_id}/test', tags=['nodes'])
async def test_node(node_id: int, db: Session = Depends(get_db)):
    row = db.query(ComputeNodeRow).filter(ComputeNodeRow.id == node_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Node not found')
    node = build_node(row)
    online = await node.health()
    row.last_status = 'online' if online else 'offline'
    db.commit()
    error = None if online else getattr(node, "last_error", None) or "节点不可达或鉴权失败"
    return {'id': node_id, 'online': online, 'type': row.type, 'base_url': row.base_url, 'error': error}
