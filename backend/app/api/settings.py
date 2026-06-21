"""设置接口：当前只管 LLM（编剧/分镜 Agent 用）。

约定：api_key 绝不明文回传前端；读取走掩码。写入时留空表示沿用旧 key。
"""
from __future__ import annotations

import os
import time

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import AiServiceConfig
from app.schemas.settings import LLMConfigIn, LLMConfigOut, LLMTestIn, LLMTestOut
from app.services.llm.client import chat_text

router = APIRouter(prefix="/settings", tags=["settings"])

_LLM = "llm"


def _active_llm(db: Session) -> AiServiceConfig | None:
    return db.scalars(
        select(AiServiceConfig)
        .where(AiServiceConfig.service_type == _LLM, AiServiceConfig.is_active.is_(True))
        .order_by(AiServiceConfig.priority.desc())
    ).first()


@router.get("/llm", response_model=LLMConfigOut)
def get_llm(db: Session = Depends(get_db)) -> LLMConfigOut:
    row = _active_llm(db)
    if row and row.base_url and row.api_key:
        return LLMConfigOut(
            configured=True,
            source="db",
            provider=row.provider,
            base_url=row.base_url,
            model=row.model,
            api_key_configured=True,
            api_key_masked="••••••••",
        )
    env_base = os.environ.get("XIGUA_LLM_BASE_URL")
    env_key = os.environ.get("XIGUA_LLM_API_KEY")
    if env_base and env_key:
        return LLMConfigOut(
            configured=True,
            source="env",
            base_url=env_base,
            model=os.environ.get("XIGUA_LLM_MODEL", "deepseek-chat"),
            api_key_configured=True,
            api_key_masked="••••••••",
        )
    return LLMConfigOut(configured=False, source="none")


@router.put("/llm", response_model=LLMConfigOut)
def put_llm(body: LLMConfigIn, db: Session = Depends(get_db)) -> LLMConfigOut:
    row = _active_llm(db)
    if row is None:
        row = AiServiceConfig(
            service_type=_LLM,
            name="default-llm",
            base_url=body.base_url,
            api_key=(body.api_key or ""),
            is_active=True,
            priority=100,
        )
        db.add(row)
    else:
        row.base_url = body.base_url
        if body.api_key:  # 留空 → 不覆盖旧 key
            row.api_key = body.api_key
    row.provider = body.provider
    row.model = body.model or "deepseek-chat"
    row.is_active = True
    db.commit()
    db.refresh(row)
    return LLMConfigOut(
        configured=bool(row.base_url and row.api_key),
        source="db",
        provider=row.provider,
        base_url=row.base_url,
        model=row.model,
        api_key_configured=bool(row.api_key),
        api_key_masked="••••••••" if row.api_key else None,
    )


@router.post("/llm/test", response_model=LLMTestOut)
def test_llm(body: LLMTestIn, db: Session = Depends(get_db)) -> LLMTestOut:
    base_url = body.base_url
    api_key = body.api_key
    model = body.model or "deepseek-chat"
    if not (base_url and api_key):  # 没传完整 → 用已存配置
        row = _active_llm(db)
        if row and row.base_url and row.api_key:
            base_url = base_url or row.base_url
            api_key = api_key or row.api_key
            model = body.model or row.model or "deepseek-chat"
        else:
            base_url = base_url or os.environ.get("XIGUA_LLM_BASE_URL")
            api_key = api_key or os.environ.get("XIGUA_LLM_API_KEY")
    if not (base_url and api_key):
        return LLMTestOut(ok=False, message="未提供 base_url / api_key，且无已存配置")
    started = time.monotonic()
    try:
        reply = chat_text(
            [{"role": "user", "content": "回复两个字：在线"}],
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=0,
            timeout=20.0,
        )
        ms = int((time.monotonic() - started) * 1000)
        return LLMTestOut(ok=True, message="连接成功", latency_ms=ms, reply=(reply or "").strip()[:40])
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = "（key 可能无效）" if code in (401, 403) else ""
        return LLMTestOut(ok=False, message=f"HTTP {code} {hint}".strip())
    except httpx.RequestError as exc:
        return LLMTestOut(ok=False, message=f"连接失败：{exc}")
    except (KeyError, ValueError) as exc:
        return LLMTestOut(ok=False, message=f"返回解析失败：{exc}")
