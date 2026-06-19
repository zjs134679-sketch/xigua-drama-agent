from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import Episode
from app.schemas.project import ScriptDraftRequest, ScriptGenerateRequest
from app.services.agents.script_agent import generate_script
from app.services.compliance import check
from app.services.llm.client import LLMNotConfigured

router = APIRouter(prefix="/script", tags=["script"])


@router.post("/generate")
def script_generate(req: ScriptGenerateRequest, db: Session = Depends(get_db)) -> dict:
    ep = db.get(Episode, req.episode_id)
    if not ep:
        raise HTTPException(404, "分集不存在")
    if not ep.content:
        raise HTTPException(400, "该分集没有小说原文")

    # 合规：小说原文先过红线
    r = check(ep.content)
    if r.blocked:
        raise HTTPException(status_code=451, detail={"blocked": True, "level": "red", "message": "原文触发红线，已拦截"})

    try:
        script = generate_script(db, ep.content, req.temperature)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")

    ep.script_content = script
    db.commit()
    return {"episode_id": ep.id, "script_content": script, "warn": r.warn}


@router.post("/draft")
def script_draft(req: ScriptDraftRequest, db: Session = Depends(get_db)) -> dict:
    """快速试写：直接收小说原文 → 合规 → LLM → 返回剧本（不落库）。"""
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(400, "请输入小说原文")

    r = check(content)
    if r.blocked:
        raise HTTPException(
            status_code=451,
            detail={
                "blocked": True,
                "level": "red",
                "message": "原文触发红线，已拦截",
                "hits": [h.__dict__ for h in r.hits],
            },
        )

    try:
        script = generate_script(db, content, req.temperature)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")

    return {"script_content": script, "warn": r.warn, "hits": [h.__dict__ for h in r.hits]}
