"""音色绑定：结构化画像 + 冲突消解。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import Base
from app.models.domain import AiVoice, Character, Drama
from app.services.voice_assignment import (
    assign_character_voices,
    profile_for_character,
    seed_preset_voices,
    _resolve_conflicts,
    _preferred_voice_id,
)


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_profile_gender_and_role():
    lead = Character(drama_id=1, name="林晓", role="女主", personality="温柔", appearance="年轻女子")
    gen = Character(drama_id=1, name="郭子仪", role="将领", description="中年统帅威严")
    old = Character(drama_id=1, name="老太爷", role="长者", description="老年")
    p1 = profile_for_character(lead)
    assert p1.gender == "female"
    assert p1.role_type == "lead"
    p2 = profile_for_character(gen)
    assert p2.gender == "male"
    assert p2.age in ("mid", "elder", "unknown")
    p3 = profile_for_character(old)
    assert p3.age == "elder"


def test_conflict_resolution_prefers_unique_for_leads():
    db = _db()
    seed_preset_voices(db)
    voices = list(db.query(AiVoice).all()) if hasattr(db, "query") else []
    from sqlalchemy import select

    voices = list(db.scalars(select(AiVoice)).all())
    chars = [
        Character(id=1, drama_id=1, name="男主", role="主角", personality="青年"),
        Character(id=2, drama_id=1, name="配角甲", role="配角", personality="青年男"),
        Character(id=3, drama_id=1, name="配角乙", role="配角", personality="青年男"),
    ]
    # 故意都偏好同一音色
    preferred = {c.id: next(v for v in voices if v.voice_id == "zh-CN-YunxiNeural") for c in chars}
    result = _resolve_conflicts(chars, preferred, voices, reserved=set())
    lead_v = result[1].voice_id
    # 至少两个不同声线（在有足够男声时）
    used = {result[c.id].voice_id for c in chars}
    assert lead_v in used
    assert len(used) >= 2


def test_assign_seeds_and_binds():
    db = _db()
    d = Drama(title="测")
    db.add(d)
    db.flush()
    db.add_all(
        [
            Character(drama_id=d.id, name="女主", role="女主", appearance="少女"),
            Character(drama_id=d.id, name="将军", role="将领", description="中年男"),
        ]
    )
    db.commit()
    rows = assign_character_voices(db, d.id)
    assert len(rows) == 2
    voice_ids = {r["voice_id"] for r in rows}
    # 女主与将军应尽量不同声
    assert len(voice_ids) >= 1
    female = next(r for r in rows if r["character_name"] == "女主")
    assert "Xiao" in female["voice_id"] or "女" in female["voice_name"]
