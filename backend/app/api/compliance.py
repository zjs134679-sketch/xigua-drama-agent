from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.system import User
from app.schemas.compliance import ComplianceCheckRequest
from app.services.compliance import check, dictionary
from app.services.compliance.enforce import sync_banned_state
from app.services.compliance.sync import sync_dictionary

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.post("/check")
def compliance_check(req: ComplianceCheckRequest) -> dict:
    return check(req.text).to_dict()


@router.get("/stats")
def compliance_stats() -> dict:
    return dictionary.stats()


@router.post("/reload")
def compliance_reload() -> dict:
    dictionary.reload()
    return {"reloaded": True, **dictionary.stats()}


@router.post("/sync")
def compliance_sync() -> dict:
    return sync_dictionary()


@router.get("/status")
def compliance_status(username: str = "local", db: Session = Depends(get_db)) -> dict:
    sync_banned_state(db, username)
    user = db.scalars(select(User).where(User.username == username)).first()
    if user is None:
        return {
            "username": username,
            "violation_count": 0,
            "banned": False,
            "banned_reason": None,
        }
    return {
        "username": user.username,
        "violation_count": user.violation_count,
        "banned": user.banned,
        "banned_reason": user.banned_reason,
    }
