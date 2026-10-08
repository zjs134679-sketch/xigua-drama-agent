from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import Episode
from app.schemas.project import ExtractRequest
from app.services.agents.extract_agent import extract, save_extracted
from app.services.compliance import FilterResult, check, enforce
from app.services.license_gate import require_valid_license
from app.services.llm.client import LLMNotConfigured

router = APIRouter(prefix="/extract", tags=["extract"])


def _hits(*results: FilterResult) -> list[dict]:
    unique: dict[tuple[str, str, str], dict] = {}
    for result in results:
        for hit in result.hits:
            key = (hit.word, hit.level, hit.category)
            unique[key] = hit.__dict__
    return list(unique.values())


def _block(
    db: Session,
    username: str | None,
    result: FilterResult,
    source: str,
    message: str,
    lic: dict | None = None,
) -> None:
    # E1：record_violation 内部票据身份优先，此处透传 lic
    enforcement = enforce.record_violation(db, username, result, source, lic=lic)
    raise HTTPException(
        status_code=451,
        detail={
            "blocked": True,
            "level": "red",
            "message": message,
            "hits": _hits(result),
            "violation_count": enforcement["violation_count"],
            "banned": enforcement["banned"],
        },
    )


@router.post("")
def run_extract(
    req: ExtractRequest,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
    # E1：身份取自票据，不再信任请求体自填的 username
    ensure_active_user(db, enforce.ticket_username(_license) or req.username)
    ep = db.get(Episode, req.episode_id)
    if not ep:
        raise HTTPException(404, "分集不存在")
    content = ep.script_content or ep.content
    if not content:
        raise HTTPException(400, "该分集没有剧本或原文")

    input_result = check(content)
    if input_result.blocked:
        _block(db, req.username, input_result, "extract_input", "内容触发红线，已拦截并记录", lic=_license)

    try:
        extracted = extract(db, content, drama_id=ep.drama_id)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")
    except (ValueError, KeyError) as e:
        raise HTTPException(502, f"提取结果解析失败: {e}")

    output_result = check(json.dumps(extracted, ensure_ascii=False))
    if output_result.blocked:
        _block(db, req.username, output_result, "extract_output", "提取结果触发红线，已拦截并记录", lic=_license)

    new = save_extracted(db, ep.drama_id, ep.id, extracted)
    return {
        "episode_id": ep.id,
        "new": new,
        "extracted": extracted,
        "warn": input_result.warn or output_result.warn,
        "hits": _hits(input_result, output_result),
    }
