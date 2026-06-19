from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.system import ComputeNode as ComputeNodeRow
from app.models.system import User
from app.schemas.compute import Text2ImageRequest
from app.services.compliance import check
from app.services.compliance.enforce import record_violation
from app.services.compute import ImageJob, get_active_node

router = APIRouter(prefix="/compute", tags=["compute"])


@router.get("/health")
async def compute_health(db: Session = Depends(get_db)) -> dict:
    node = get_active_node(db)
    return {"type": node.type, "base_url": node.base_url, "online": await node.health()}


@router.get("/nodes")
def list_nodes(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ComputeNodeRow).order_by(ComputeNodeRow.priority.desc())).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "type": r.type,
            "base_url": r.base_url,
            "priority": r.priority,
            "is_active": r.is_active,
            "last_status": r.last_status,
        }
        for r in rows
    ]


@router.post("/text2image")
async def text2image(req: Text2ImageRequest, db: Session = Depends(get_db)):
    # 0. 封号校验
    user = db.scalars(select(User).where(User.username == (req.username or "local"))).first()
    if user and user.banned:
        raise HTTPException(status_code=403, detail={"banned": True, "message": "账号已封禁，无法继续使用"})

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
