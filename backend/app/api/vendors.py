"""多厂商模型管理 API：供应商/服务配置 CRUD + 模型测试。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import AiServiceConfig, AiServiceProvider
from app.models.system import ComputeNode

router = APIRouter(prefix="/vendors", tags=["vendors"])


# ── DTO ──────────────────────────────────────────────────────────────
class VendorConfigCreate(BaseModel):
    service_type: str  # image/video/tts/llm
    name: str
    provider: str | None = None
    base_url: str
    api_key: str = ""
    model: str | None = None
    endpoint: str | None = None
    query_endpoint: str | None = None
    priority: int = 0
    is_default: bool = False
    is_active: bool = True
    settings: str | None = None


class VendorConfigUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    endpoint: str | None = None
    query_endpoint: str | None = None
    priority: int | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    settings: str | None = None


class ProviderCreate(BaseModel):
    name: str
    display_name: str | None = None
    service_type: str
    provider: str
    default_url: str | None = None
    preset_models: str | None = None
    description: str | None = None


class ModelTestInput(BaseModel):
    service_type: str  # text / image / video
    base_url: str
    api_key: str = ""
    model: str | None = None
    prompt: str | None = None


# ── Helper ────────────────────────────────────────────────────────────
def _config_view(c: AiServiceConfig) -> dict:
    return {
        "id": c.id,
        "service_type": c.service_type,
        "name": c.name,
        "provider": c.provider,
        "base_url": c.base_url,
        "api_key_configured": bool(c.api_key),
        "api_key_masked": "••••••••" if c.api_key else None,
        "model": c.model,
        "endpoint": c.endpoint,
        "query_endpoint": c.query_endpoint,
        "priority": c.priority,
        "is_default": c.is_default,
        "is_active": c.is_active,
        "settings": c.settings,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _provider_view(p: AiServiceProvider) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "display_name": p.display_name,
        "service_type": p.service_type,
        "provider": p.provider,
        "default_url": p.default_url,
        "preset_models": p.preset_models,
        "description": p.description,
        "is_active": p.is_active,
    }


# ── 预设供应商 ───────────────────────────────────────────────────────
@router.get("/providers")
def list_providers(service_type: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = select(AiServiceProvider)
    if service_type:
        q = q.where(AiServiceProvider.service_type == service_type)
    rows = db.scalars(q.order_by(AiServiceProvider.id)).all()
    return [_provider_view(r) for r in rows]


@router.post("/providers")
def create_provider(body: ProviderCreate, db: Session = Depends(get_db)) -> dict:
    row = AiServiceProvider(**body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _provider_view(row)


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(AiServiceProvider, provider_id)
    if not row:
        raise HTTPException(404, "供应商不存在")
    db.delete(row)
    db.commit()
    return {"deleted": True}


# ── 用户服务配置 CRUD ────────────────────────────────────────────────
@router.get("/configs")
def list_configs(service_type: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = select(AiServiceConfig)
    if service_type:
        q = q.where(AiServiceConfig.service_type == service_type)
    rows = db.scalars(q.order_by(AiServiceConfig.priority.desc())).all()
    return [_config_view(r) for r in rows]


@router.post("/configs")
def create_config(body: VendorConfigCreate, db: Session = Depends(get_db)) -> dict:
    row = AiServiceConfig(**body.model_dump(exclude={"api_key"}), api_key=body.api_key)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _config_view(row)


@router.put("/configs/{config_id}")
def update_config(config_id: int, body: VendorConfigUpdate, db: Session = Depends(get_db)) -> dict:
    row = db.get(AiServiceConfig, config_id)
    if not row:
        raise HTTPException(404, "配置不存在")
    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _config_view(row)


@router.delete("/configs/{config_id}")
def delete_config(config_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(AiServiceConfig, config_id)
    if not row:
        raise HTTPException(404, "配置不存在")
    db.delete(row)
    db.commit()
    return {"deleted": True}


@router.post("/configs/{config_id}/test")
async def test_config(config_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(AiServiceConfig, config_id)
    if not row:
        raise HTTPException(404, "配置不存在")
    import time
    try:
        import httpx
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=10) as client:
            headers = {}
            if row.api_key and row.api_key.strip():
                headers["Authorization"] = f"Bearer {row.api_key}"
            test_url = row.base_url.rstrip("/")
            if row.endpoint:
                test_url = test_url + row.endpoint
            r = await client.get(test_url, headers=headers)
            latency = round((time.monotonic() - start) * 1000)
            online = r.status_code < 500
            return {
                "id": config_id,
                "online": online,
                "status_code": r.status_code,
                "latency_ms": latency,
                "error": None if online else f"HTTP {r.status_code}",
            }
    except Exception as exc:
        return {"id": config_id, "online": False, "status_code": 0, "latency_ms": None, "error": str(exc)}


@router.post("/test")
async def model_test(body: ModelTestInput, db: Session = Depends(get_db)) -> dict:
    """通用模型测试——文字/图片/视频均可。"""
    import time
    try:
        import httpx
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {"Content-Type": "application/json"}
            if body.api_key and body.api_key.strip():
                headers["Authorization"] = f"Bearer {body.api_key}"

            test_url = body.base_url.rstrip("/")
            if body.service_type == "text":
                payload = {"messages": [{"role": "user", "content": body.prompt or "Hello"}], "model": body.model or "gpt-3.5-turbo"}
                r = await client.post(f"{test_url}/v1/chat/completions", headers=headers, json=payload)
                ok = r.status_code == 200
                reply = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")[:200] if ok else None
            elif body.service_type in ("image", "video"):
                payload = {"prompt": body.prompt or "A beautiful landscape", "model": body.model or "default"}
                r = await client.post(f"{test_url}/generate", headers=headers, json=payload)
                ok = r.status_code < 500
                reply = None
            else:
                return {"ok": False, "message": f"不支持的服务类型: {body.service_type}"}

            latency = round((time.monotonic() - start) * 1000)
            return {"ok": ok, "message": "OK" if ok else f"HTTP {r.status_code}", "latency_ms": latency, "reply": reply}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "latency_ms": None, "reply": None}


# ── 列出厂商可用模型 ──────────────────────────────────────────────────
from app.models.system import ComputeNode as ComputeNodeRow


@router.get("/models")
def list_models(service_type: str = "image", db: Session = Depends(get_db)) -> list[dict]:
    """汇总所有可用模型：预设供应商 + 用户配置 + 算力节点。"""
    models: list[dict] = []

    # 从预设供应商
    providers = db.scalars(select(AiServiceProvider).where(AiServiceProvider.service_type == service_type, AiServiceProvider.is_active == True)).all()
    for p in providers:
        if p.preset_models:
            try:
                import json
                preset = json.loads(p.preset_models)
                for m in (preset if isinstance(preset, list) else [preset]):
                    if isinstance(m, dict):
                        models.append({"provider": p.name, "service_type": service_type, "model": m.get("name", str(m)), "preset": True, "config_id": None})
                    else:
                        models.append({"provider": p.name, "service_type": service_type, "model": str(m), "preset": True, "config_id": None})
            except Exception:
                pass

    # 从用户配置
    configs = db.scalars(select(AiServiceConfig).where(AiServiceConfig.service_type == service_type, AiServiceConfig.is_active == True)).all()
    for c in configs:
        if c.model:
            models.append({"provider": c.name, "service_type": service_type, "model": c.model, "preset": False, "config_id": c.id})

    # 从算力节点
    if service_type == "image":
        nodes = db.scalars(select(ComputeNodeRow).where(ComputeNodeRow.is_active == True)).all()
        for n in nodes:
            models.append({"provider": n.name, "service_type": "image", "model": n.model or n.type, "preset": False, "config_id": n.id})

    return models
