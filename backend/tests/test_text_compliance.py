from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import extract as extract_api
from app.api import script as script_api
from app.api.extract import run_extract
from app.api.script import script_draft
from app.core.db import Base
from app.models.domain import Episode
from app.models.system import Violation
from app.schemas.project import ExtractRequest, ScriptDraftRequest
from app.services.compliance import enforce
from app.services.compliance.filter import FilterResult, Hit


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_script_llm_red_output_is_blocked_and_recorded_once(monkeypatch: pytest.MonkeyPatch):
    db = make_db()
    blocked = FilterResult("red", [Hit("占位输出红线词", "red", "测试")])
    passed = FilterResult("pass", [])
    monkeypatch.setattr(script_api, "ensure_active_user", lambda *_args: None)
    monkeypatch.setattr(script_api, "generate_script", lambda *_args: "剧本含占位输出红线词")
    monkeypatch.setattr(script_api, "check", lambda text: blocked if "占位输出红线词" in text else passed)
    monkeypatch.setattr(enforce, "report_to_auth", lambda *_args: None)

    with pytest.raises(HTTPException) as raised:
        script_draft(ScriptDraftRequest(content="占位小说原文", username="text-user"), db)

    assert raised.value.status_code == 451
    assert raised.value.detail["violation_count"] == 1
    violations = db.scalars(select(Violation)).all()
    assert len(violations) == 1
    assert violations[0].source == "script_output"
    assert violations[0].word != blocked.hits[0].word
    db.close()


def test_extract_llm_yellow_output_returns_warning(monkeypatch: pytest.MonkeyPatch):
    db = make_db()
    episode = Episode(
        drama_id=1,
        episode_number=1,
        title="占位分集",
        script_content="占位剧本正文",
    )
    db.add(episode)
    db.commit()
    warning = FilterResult("yellow", [Hit("占位输出黄线词", "yellow", "测试")])
    passed = FilterResult("pass", [])
    extracted = {
        "characters": [{"name": "占位输出黄线词"}],
        "scenes": [],
        "props": [],
    }
    monkeypatch.setattr(extract_api, "ensure_active_user", lambda *_args: None)
    monkeypatch.setattr(extract_api, "extract", lambda *_args, **_kw: extracted)
    monkeypatch.setattr(extract_api, "check", lambda text: warning if "占位输出黄线词" in text else passed)
    monkeypatch.setattr(
        extract_api,
        "save_extracted",
        lambda *_args: {"characters": 1, "scenes": 0, "props": 0},
    )

    response = run_extract(ExtractRequest(episode_id=episode.id, username="text-user"), db)

    assert response["warn"] is True
    assert response["hits"] == [warning.hits[0].__dict__]
    assert db.scalars(select(Violation)).all() == []
    db.close()


def test_extract_llm_red_output_is_not_saved(monkeypatch: pytest.MonkeyPatch):
    db = make_db()
    episode = Episode(drama_id=1, episode_number=1, title="占位分集", content="占位正文")
    db.add(episode)
    db.commit()
    blocked = FilterResult("red", [Hit("占位提取红线词", "red", "测试")])
    passed = FilterResult("pass", [])
    extracted = {"characters": [], "scenes": [], "props": [{"name": "占位提取红线词"}]}
    monkeypatch.setattr(extract_api, "ensure_active_user", lambda *_args: None)
    monkeypatch.setattr(extract_api, "extract", lambda *_args, **_kw: extracted)
    monkeypatch.setattr(extract_api, "check", lambda text: blocked if "占位提取红线词" in text else passed)
    monkeypatch.setattr(enforce, "report_to_auth", lambda *_args: None)

    def should_not_save(*_args):
        raise AssertionError("红线提取结果不得落库")

    monkeypatch.setattr(extract_api, "save_extracted", should_not_save)

    with pytest.raises(HTTPException) as raised:
        run_extract(ExtractRequest(episode_id=episode.id, username="extract-user"), db)

    assert raised.value.status_code == 451
    assert db.scalars(select(Violation)).one().source == "extract_output"
    db.close()
