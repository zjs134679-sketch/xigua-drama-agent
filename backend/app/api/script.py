from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import Episode
from app.schemas.project import ScriptDraftRequest, ScriptGenerateRequest
from app.services.agents.script_agent import generate_script
from app.services.compliance import FilterResult, check, enforce
from app.services.license_gate import require_valid_license
from app.services.llm.client import LLMNotConfigured

router = APIRouter(prefix="/script", tags=["script"])


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
) -> None:
    enforcement = enforce.record_violation(db, username, result, source)
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


@router.post("/generate")
def script_generate(
    req: ScriptGenerateRequest,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
    ensure_active_user(db, req.username)
    ep = db.get(Episode, req.episode_id)
    if not ep:
        raise HTTPException(404, "分集不存在")
    if not ep.content:
        raise HTTPException(400, "该分集没有小说原文")

    # 合规：小说原文先过红线
    input_result = check(ep.content)
    if input_result.blocked:
        _block(db, req.username, input_result, "script_input", "原文触发红线，已拦截并记录")

    try:
        script = generate_script(db, ep.content, req.temperature, episode_id=ep.id)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            raise HTTPException(
                502,
                f"LLM 鉴权失败（HTTP {code}）：API Key 无效或已过期，请到「设置 → 语言模型」更新并测试连接",
            ) from e
        raise HTTPException(502, f"LLM 调用失败: HTTP {code}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}") from e

    output_result = check(script)
    if output_result.blocked:
        _block(db, req.username, output_result, "script_output", "生成剧本触发红线，已拦截并记录")

    ep.script_content = script
    db.commit()
    return {
        "episode_id": ep.id,
        "script_content": script,
        "warn": input_result.warn or output_result.warn,
        "hits": _hits(input_result, output_result),
    }


@router.post("/draft")
def script_draft(req: ScriptDraftRequest, db: Session = Depends(get_db)) -> dict:
    """快速试写：直接收小说原文 → 合规 → LLM → 返回剧本（不落库）。"""
    ensure_active_user(db, req.username)
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(400, "请输入小说原文")

    input_result = check(content)
    if input_result.blocked:
        _block(db, req.username, input_result, "script_input", "原文触发红线，已拦截并记录")

    try:
        script = generate_script(db, content, req.temperature)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")

    output_result = check(script)
    if output_result.blocked:
        _block(db, req.username, output_result, "script_output", "生成剧本触发红线，已拦截并记录")

    return {
        "script_content": script,
        "warn": input_result.warn or output_result.warn,
        "hits": _hits(input_result, output_result),
    }
