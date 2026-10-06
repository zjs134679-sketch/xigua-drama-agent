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
from app.services.compliance.enforce import record_violation, ticket_username
from app.services.compute import ImageJob, get_active_node
from app.services.license_gate import require_valid_license

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
    from app.services.compute.registry import describe_compute_readiness

    image_node = get_active_node(db, capability="image")
    video_node = get_active_node(db, capability="video")
    image_online = await image_node.health()
    video_online = await video_node.health()
    # 兼容旧字段：任一路在线即 online
    primary = image_node if image_online else video_node
    readiness = describe_compute_readiness(db)
    return {
        "type": primary.type,
        "base_url": primary.base_url,
        "online": image_online or video_online,
        "image": {
            "type": image_node.type,
            "base_url": getattr(image_node, "base_url", None),
            "provider": getattr(image_node, "provider", None),
            "online": image_online,
        },
        "video": {
            "type": video_node.type,
            "base_url": getattr(video_node, "base_url", None),
            "provider": getattr(video_node, "provider", None),
            "online": video_online,
        },
        "readiness": readiness,
    }


@router.get("/diagnose")
async def compute_diagnose(node_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    """检测 Comfy 连通性与 LTX/Wan/Flux 关键模型是否疑似齐全。"""
    from app.services.comfy_diagnostics import diagnose_comfy

    return await diagnose_comfy(db, node_id=node_id)


@router.get("/nodes")
def list_nodes(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ComputeNodeRow).order_by(ComputeNodeRow.priority.desc())).all()
    return [_node_payload(row) for row in rows]


@router.post("/text2image")
async def text2image(
    req: Text2ImageRequest,
    db: Session = Depends(get_db),
    lic: dict = Depends(require_valid_license),
):
    # E1：username 取自票据身份，不再信任请求体（schema 已移除 username 字段）
    username = ticket_username(lic)
    # 0. 封号校验
    ensure_active_user(db, username)

    # 1. 合规过滤（对最终绘画提示词）
    result = check(req.prompt)
    if result.blocked:
        enf = record_violation(db, username, result, "image_prompt", lic=lic)
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

    # 2. 送算力节点生成（优先会出图的节点：云万相 / Comfy）
    node = get_active_node(db, capability="image")
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


from pydantic import BaseModel, Field  # noqa: E402


class CloudQuickSetupBody(BaseModel):
    wan_api_key: str | None = None
    seedance_api_key: str | None = None
    wan_model: str | None = None
    seedance_model: str | None = None
    deactivate_comfy: bool = False
    test_connection: bool = True


@router.get("/cloud-status", tags=["nodes"])
def cloud_status(db: Session = Depends(get_db)) -> dict:
    from app.services.cloud_quick_setup import cloud_status as _status

    return _status(db)


@router.post("/cloud-quick-setup", tags=["nodes"])
async def cloud_quick_setup(body: CloudQuickSetupBody, db: Session = Depends(get_db)) -> dict:
    """小白：填通义/豆包 Key → 自动建云出图+云出片节点。"""
    from app.services.cloud_quick_setup import quick_setup_cloud

    try:
        return await quick_setup_cloud(
            db,
            wan_api_key=body.wan_api_key,
            seedance_api_key=body.seedance_api_key,
            wan_model=body.wan_model,
            seedance_model=body.seedance_model,
            deactivate_comfy=body.deactivate_comfy,
            test_connection=body.test_connection,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    return {"ok": True}


class ComfyExportBody(BaseModel):
    """导出调试工作流到 ComfyUI。"""
    comfy_user_dir: str | None = None


class ComfySyncBody(BaseModel):
    """从 Comfy history 或本地工作流文件同步参数。"""
    source: str = Field(default="history", description="history | file")
    file_path: str | None = None
    apply: bool = True  # 是否写回节点 model_settings


@router.post("/nodes/{node_id}/export-comfy-workflows", tags=["nodes"])
def export_comfy_workflows(node_id: int, body: ComfyExportBody | None = None, db: Session = Depends(get_db)):
    """导出「角色出图 + 多参考视频」两个 API 工作流到 ComfyUI workflows，便于界面调试。"""
    from app.services.comfy_workflow_sync import export_debug_workflows

    row = db.query(ComputeNodeRow).filter(ComputeNodeRow.id == node_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    if row.type not in ("local_comfy", "remote_comfy"):
        raise HTTPException(400, "仅本地/远程 Comfy 节点支持导出调试工作流")
    extra = _extra_payload(row)
    ms = extra.get("model_settings") if isinstance(extra.get("model_settings"), dict) else {}
    body = body or ComfyExportBody()
    try:
        result = export_debug_workflows(
            model_settings=ms,
            comfy_user_dir_hint=body.comfy_user_dir or ms.get("comfy_user_dir"),
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    # 记住 comfy 目录
    if result.get("comfy_workflows_dir"):
        ms = dict(ms)
        # 存 user/default 根，便于下次
        from pathlib import Path as _P

        wdir = _P(result["comfy_workflows_dir"])
        ms["comfy_user_dir"] = str(wdir)
        row.extra = _merge_extra(row.extra, model_settings=ms)
        db.commit()
    return result


@router.post("/nodes/{node_id}/sync-comfy-params", tags=["nodes"])
async def sync_comfy_params(node_id: int, body: ComfySyncBody, db: Session = Depends(get_db)):
    """从 Comfy 最近任务或工作流文件读取参数，可选写回算力节点 model_settings。"""
    from app.services.comfy_workflow_sync import (
        extract_from_workflow_file,
        fetch_latest_history_params,
        params_to_model_settings,
    )

    row = db.query(ComputeNodeRow).filter(ComputeNodeRow.id == node_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    if row.type not in ("local_comfy", "remote_comfy"):
        raise HTTPException(400, "仅 Comfy 节点可同步参数")

    extra = _extra_payload(row)
    ms = extra.get("model_settings") if isinstance(extra.get("model_settings"), dict) else {}

    if body.source == "file":
        if not body.file_path:
            raise HTTPException(400, "请提供 file_path")
        try:
            # D1：file_path 必须落在白名单目录内（应用自带工作流目录 / 该节点 Comfy 用户目录），
            # 拒绝绝对路径与目录跳出，防止读取任意本地文件。
            from pathlib import Path as _Path

            from app.core.config import settings as _settings
            from app.services.comfy_workflow_sync import resolve_workflow_file_in_allowlist

            _allow_dirs = [_Path(_settings.workflows_dir)]
            _comfy_user_dir = ms.get("comfy_user_dir")
            if _comfy_user_dir:
                _allow_dirs.append(_Path(_comfy_user_dir))
            _safe_path = resolve_workflow_file_in_allowlist(body.file_path, _allow_dirs)
            extracted = extract_from_workflow_file(_safe_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"读取工作流失败: {exc}") from exc
        if not extracted.get("ok"):
            raise HTTPException(400, extracted.get("message") or "解析失败")
        params = extracted.get("params") or {}
        source_info = extracted.get("source")
    else:
        if not row.base_url:
            raise HTTPException(400, "节点未配置 base_url")
        try:
            extracted = await fetch_latest_history_params(row.base_url, token=row.token)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"连接 Comfy 失败: {exc}") from exc
        if not extracted.get("ok"):
            return {
                "ok": False,
                "message": extracted.get("message") or "同步失败",
                "detail": extracted,
            }
        params = extracted.get("params") or {}
        source_info = extracted.get("prompt_id")

    merged = params_to_model_settings(params, ms)
    applied = False
    if body.apply:
        row.extra = _merge_extra(row.extra, model_settings=merged)
        db.commit()
        db.refresh(row)
        applied = True

    return {
        "ok": True,
        "applied": applied,
        "source": body.source,
        "source_info": source_info,
        "params": params,
        "model_settings": merged,
        "node": _node_payload(row) if applied else None,
        "message": (
            "已写回算力节点：角色出图/默认步数与尺寸将采用同步值；"
            "角色卡片仍可单独再调更高分辨率。"
            if applied
            else "已解析参数（未写回，apply=false）"
        ),
    }


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
