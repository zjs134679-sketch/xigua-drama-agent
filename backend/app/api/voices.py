from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.domain import AiVoice
from app.services.tts import preview_voice

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


class VoicePreviewRequest(BaseModel):
    voice_id: str
    text: str | None = None


@router.post("/preview")
async def preview(body: VoicePreviewRequest) -> dict:
    """试听某个音色：合成一小段样音，返回可播放的 /oss 地址。"""
    if not body.voice_id.strip():
        raise HTTPException(400, "缺少音色")
    try:
        url = await preview_voice(body.voice_id, body.text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"试听合成失败：{exc}")
    return {"audio_url": url}
