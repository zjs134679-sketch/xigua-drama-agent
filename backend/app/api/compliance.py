from __future__ import annotations

from fastapi import APIRouter

from app.schemas.compliance import ComplianceCheckRequest
from app.services.compliance import check, dictionary

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
