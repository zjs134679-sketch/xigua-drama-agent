"""素材生成测试仅使用占位文本。"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import ArtStyle, Asset, Character, Scene
from app.models.system import Violation
from app.services import asset_generation
from app.services.asset_generation import ComplianceBlocked, build_character_prompt, build_scene_prompt
from app.services.compliance import enforce
from app.services.compliance.filter import FilterResult, Hit
from app.services.compute import JobResult


def test_prompt_composition_contains_target_and_style():
    style = ArtStyle(name="占位画风", prompt_suffix="柔和光影")
    character = Character(drama_id=1, name="角色甲", appearance="短发，蓝色外套")
    scene = Scene(drama_id=1, location="旧车站", time="傍晚", prompt="远处有列车灯光")

    character_prompt = build_character_prompt(character, None, style, scene, "站在月台上")
    scene_prompt = build_scene_prompt(scene, "低机位", style)

    assert "柔和光影" in character_prompt
    assert "短发，蓝色外套" in character_prompt
    assert "旧车站" in character_prompt
    assert "站在月台上" in character_prompt
    assert "柔和光影" in scene_prompt
    assert "远处有列车灯光" in scene_prompt
    assert "低机位" in scene_prompt


def test_red_prompt_is_recorded_and_never_sent(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    result = FilterResult("red", [Hit("占位命中词", "red", "测试")])
    monkeypatch.setattr(asset_generation, "check", lambda _prompt: result)
    monkeypatch.setattr(enforce, "report_to_auth", lambda *_args: None)

    def should_not_select_node(_db):
        raise AssertionError("红线 prompt 不得进入算力节点")

    monkeypatch.setattr(asset_generation, "get_active_node", should_not_select_node)

    with pytest.raises(ComplianceBlocked):
        asyncio.run(
            asset_generation.generate_scene_asset(
                db,
                custom_prompt="占位生成内容",
                username="test-user",
            )
        )

    violation = db.scalars(select(Violation)).one()
    assert violation.word == "占****"
    assert violation.word != result.hits[0].word
    db.close()


def test_yellow_multireference_generation_keeps_reference_order(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    style = ArtStyle(name="占位画风", prompt_suffix="柔和光影")
    character = Character(
        drama_id=7,
        name="角色乙",
        appearance="深色长发",
        image_url="character-reference.png",
    )
    scene = Scene(
        drama_id=7,
        location="河畔",
        time="清晨",
        prompt="薄雾与逆光",
        local_path="scene-reference.png",
    )
    db.add_all([style, character, scene])
    db.commit()

    class FakeNode:
        job = None

        async def text2image(self, job):
            self.job = job
            return JobResult("completed", image_path="output.png", image_url="/view/output.png")

    node = FakeNode()
    warning = FilterResult("yellow", [Hit("占位提示词", "yellow", "测试")])
    monkeypatch.setattr(asset_generation, "check", lambda _prompt: warning)
    monkeypatch.setattr(asset_generation, "get_active_node", lambda _db: node)

    outcome = asyncio.run(
        asset_generation.generate_character_asset(
            db,
            character_id=character.id,
            scene_id=scene.id,
            art_style_id=style.id,
            action="沿河行走",
        )
    )

    assert node.job.workflow == asset_generation.KONTEXT_WORKFLOW
    assert node.job.reference_images == ["scene-reference.png", "character-reference.png"]
    assert outcome.compliance.warn
    assert character.local_path == "output.png"
    assert db.scalars(select(Asset)).one().category == "character"
    db.close()
