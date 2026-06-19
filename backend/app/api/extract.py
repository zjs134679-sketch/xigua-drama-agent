from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import Episode
from app.schemas.project import ExtractRequest
from app.services.agents.extract_agent import extract, save_extracted
from app.services.compliance import check
from app.services.llm.client import LLMNotConfigured

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("")
def run_extract(req: ExtractRequest, db: Session = Depends(get_db)) -> dict:
    ep = db.get(Episode, req.episode_id)
    if not ep:
        raise HTTPException(404, "分集不存在")
    content = ep.script_content or ep.content
    if not content:
        raise HTTPException(400, "该分集没有剧本或原文")

    r = check(content)
    if r.blocked:
        raise HTTPException(status_code=451, detail={"blocked": True, "level": "red", "message": "内容触发红线，已拦截"})

    try:
        extracted = extract(db, content)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")
    except (ValueError, KeyError) as e:
        raise HTTPException(502, f"提取结果解析失败: {e}")

    new = save_extracted(db, ep.drama_id, ep.id, extracted)
    return {"episode_id": ep.id, "new": new, "extracted": extracted}
