from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import AiVoice

router = APIRouter(prefix="/voices", tags=["voices"])


@router.get("")
def list_voices(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(AiVoice).order_by(AiVoice.id)).all()
    return [
        {
            "id": row.id,
            "voice_id": row.voice_id,
            "voice_name": row.voice_name,
            "description": row.description,
            "language": row.language,
            "provider": row.provider,
        }
        for row in rows
    ]
