"""素材生成测试仅使用占位文本。"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import (
    ArtStyle,
    Asset,
    Character,
    Drama,
    Episode,
    EpisodeCharacter,
    ImageGeneration,
    Scene,
    Storyboard,
    StoryboardCharacter,
)
from app.models.system import Violation
from app.services import asset_generation
from app.services.asset_generation import ComplianceBlocked, build_character_prompt, build_scene_prompt
from app.services.compliance import enforce
from app.services.compliance.filter import FilterResult, Hit
from app.services.compute import JobResult
from app.services.storyboard_references import resolve_storyboard_image_references


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
    assert "empty environment plate" in scene_prompt


def test_red_prompt_is_recorded_and_never_sent(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    result = FilterResult("red", [Hit("占位命中词", "red", "测试")])
    monkeypatch.setattr(asset_generation, "check", lambda _prompt: result)
    monkeypatch.setattr(enforce, "report_to_auth", lambda *_args: None)

    def should_not_select_node(_db, _node_id=None):
        raise AssertionError("红线 prompt 不得进入算力节点")

    monkeypatch.setattr(asset_generation, "get_node", should_not_select_node)

    with pytest.raises(ComplianceBlocked):
        asyncio.run(
            asset_generation.generate_scene_asset(
                db,
                full_prompt="占位生成内容",
                username="test-user",
            )
        )

    violation = db.scalars(select(Violation)).one()
    assert violation.word == "占****"
    assert violation.word != result.hits[0].word
    db.close()


def test_smart_references_use_scene_anchor_previous_shot_and_current_characters():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    episode = Episode(drama_id=21, episode_number=1, title="邮局", script_content="占位")
    scene = Scene(drama_id=21, location="邮局", time="清晨", prompt="柜台", image_url="scene.png")
    old_man = Character(drama_id=21, name="老周", image_url="old-man.png")
    girl = Character(drama_id=21, name="女孩", image_url="girl.png")
    db.add_all([episode, scene, old_man, girl])
    db.flush()
    db.add_all([
        EpisodeCharacter(episode_id=episode.id, character_id=old_man.id),
        EpisodeCharacter(episode_id=episode.id, character_id=girl.id),
    ])
    shots = [
        Storyboard(episode_id=episode.id, scene_id=scene.id, storyboard_number=1, title="建立", location="邮局", time="清晨，阳光斜照", image_prompt="room", composed_image="shot-1.png"),
        Storyboard(episode_id=episode.id, scene_id=scene.id, storyboard_number=2, title="进门", location="邮局", time="清晨，阳光斜照", image_prompt="girl enters", composed_image="shot-2.png"),
        Storyboard(episode_id=episode.id, scene_id=scene.id, storyboard_number=3, title="询问", location="邮局", time="清晨，阳光斜照", image_prompt="two shot", composed_image="shot-3.png"),
        Storyboard(episode_id=episode.id, scene_id=scene.id, storyboard_number=4, title="回答", location="邮局", time="清晨，阳光斜照", image_prompt="reply"),
    ]
    db.add_all(shots)
    db.flush()
    db.add_all([
        StoryboardCharacter(storyboard_id=shots[2].id, character_id=old_man.id),
        StoryboardCharacter(storyboard_id=shots[2].id, character_id=girl.id),
        StoryboardCharacter(storyboard_id=shots[3].id, character_id=old_man.id),
        StoryboardCharacter(storyboard_id=shots[3].id, character_id=girl.id),
    ])
    db.commit()

    rows, mode = resolve_storyboard_image_references(db, shots[3])

    assert mode == "auto"
    assert [(row.kind, row.url) for row in rows] == [
        ("continuity", "shot-1.png"),
        ("previous", "shot-3.png"),
    ]
    db.close()


def test_smart_references_break_on_time_jump_and_manual_override_wins():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    episode = Episode(drama_id=22, episode_number=1, title="跳时", script_content="占位")
    scene = Scene(drama_id=22, location="院子", time="夜", prompt="院子", image_url="night-yard.png")
    character = Character(drama_id=22, name="甲", image_url="a.png")
    first = Storyboard(episode_id=1, scene_id=1, storyboard_number=1, title="白天", location="院子", time="上午", image_prompt="day", composed_image="day.png")
    second = Storyboard(episode_id=1, scene_id=1, storyboard_number=2, title="次日夜晚", location="院子", time="夜", image_prompt="night")
    db.add_all([episode, scene, character])
    db.flush()
    first.episode_id = episode.id
    second.episode_id = episode.id
    first.scene_id = scene.id
    second.scene_id = scene.id
    db.add_all([first, second])
    db.flush()
    db.add_all([
        EpisodeCharacter(episode_id=episode.id, character_id=character.id),
        StoryboardCharacter(storyboard_id=second.id, character_id=character.id),
    ])
    db.commit()

    rows, mode = resolve_storyboard_image_references(db, second)
    assert mode == "auto"
    assert all(row.kind not in ("continuity", "previous") for row in rows)
    assert [row.url for row in rows] == ["night-yard.png", "a.png"]

    second.reference_images = '["day.png"]'
    db.commit()
    rows, mode = resolve_storyboard_image_references(db, second)
    assert mode == "manual"
    assert [row.url for row in rows] == ["day.png"]
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
        type = "local_comfy"
        job = None

        async def text2image(self, job):
            self.job = job
            return JobResult("completed", image_path="output.png", image_url="/view/output.png")

    node = FakeNode()
    warning = FilterResult("yellow", [Hit("占位提示词", "yellow", "测试")])
    monkeypatch.setattr(asset_generation, "check", lambda _prompt: warning)
    monkeypatch.setattr(asset_generation, "get_node", lambda _db, _node_id=None: node)

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


def test_storyboard_uses_scene_and_all_mentioned_character_references(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    episode = Episode(drama_id=9, episode_number=1, title="第一集", script_content="占位剧本")
    db.add(episode)
    db.flush()
    scene = Scene(
        drama_id=9, episode_id=episode.id, location="军营", time="夜",
        prompt="营火", local_path="scene-reference.png",
    )
    first = Character(drama_id=9, name="角色甲", image_url="character-a.png")
    second = Character(drama_id=9, name="角色乙", local_path="character-b.png")
    db.add_all([scene, first, second])
    db.flush()
    db.add_all([
        EpisodeCharacter(episode_id=episode.id, character_id=first.id),
        EpisodeCharacter(episode_id=episode.id, character_id=second.id),
    ])
    storyboard = Storyboard(
        episode_id=episode.id, storyboard_number=1, title="营中对话", location="军营",
        action="角色甲握刀，角色乙拍他的肩膀", dialogue="角色乙：稳住。",
        image_prompt="Two soldiers beside a campfire", duration=6,
    )
    db.add(storyboard)
    db.commit()

    class FakeNode:
        type = "local_comfy"
        job = None

        async def text2image(self, job):
            self.job = job
            return JobResult("completed", image_path="output.png", image_url="/view/output.png")

    node = FakeNode()
    monkeypatch.setattr(asset_generation, "get_node", lambda _db, _node_id=None: node)

    outcome = asyncio.run(asset_generation.generate_storyboard_image(db, storyboard_id=storyboard.id))

    assert node.job.workflow == asset_generation.KONTEXT_WORKFLOW
    assert node.job.reference_images == ["scene-reference.png", "character-a.png", "character-b.png"]
    assert "Reference 2 is character 角色甲" in node.job.prompt
    assert "Reference 3 is character 角色乙" in node.job.prompt
    assert "Reference 1 controls architecture, lighting and environment only" in node.job.prompt
    assert "Reference 2 is 角色甲, appearing exactly once at left foreground" in node.job.prompt
    assert "Reference 3 is 角色乙, appearing exactly once at right foreground" in node.job.prompt
    assert "copy that reference's exact face, age, hair, facial hair and costume" in node.job.prompt
    assert "same scale and focal depth" in node.job.prompt
    assert "never form a row" in node.job.prompt
    assert node.job.negative == asset_generation.STORYBOARD_NEGATIVE_PROMPT
    assert outcome.workflow == asset_generation.KONTEXT_WORKFLOW
    linked = db.scalars(
        select(StoryboardCharacter.character_id)
        .where(StoryboardCharacter.storyboard_id == storyboard.id)
        .order_by(StoryboardCharacter.character_id)
    ).all()
    assert linked == [first.id, second.id]
    generation = db.scalars(select(ImageGeneration).where(ImageGeneration.storyboard_id == storyboard.id)).one()
    assert generation.reference_images == '["scene-reference.png", "character-a.png", "character-b.png"]'
    db.close()


def test_character_custom_prompt_keeps_default_style_and_historical_period(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    drama = Drama(id=12, title="唐军收复长安", style="realistic")
    style = ArtStyle(name="写实电影感", prompt_suffix="cinematic realistic film lighting", sort_order=0)
    character = Character(
        drama_id=12,
        name="少年兵",
        appearance="身穿破旧唐军战甲，手持长矛",
        image_prompt="a young soldier",
    )
    db.add_all([drama, style, character])
    db.commit()

    class FakeNode:
        type = "local_comfy"
        job = None

        async def text2image(self, job):
            self.job = job
            return JobResult("completed", image_path="period-output.png", image_url="/view/period-output.png")

    node = FakeNode()
    monkeypatch.setattr(asset_generation, "get_node", lambda _db, _node_id=None: node)

    asyncio.run(asset_generation.generate_character_asset(
        db,
        character_id=character.id,
        full_prompt="a slim young soldier, full body",
    ))

    assert "cinematic realistic film lighting" in node.job.prompt
    assert "teenage male aged 16 to 18" in node.job.prompt
    assert "no beard" in node.job.prompt
    assert "8th-century Tang dynasty" in node.job.prompt
    assert "strictly no modern military uniform" in node.job.prompt
    assert "modern military uniform" in node.job.negative
    db.close()
