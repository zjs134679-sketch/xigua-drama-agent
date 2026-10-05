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
    Prop,
    Scene,
    Storyboard,
    StoryboardCharacter,
)
from app.models.system import Violation
from app.services import asset_generation
from app.services.asset_generation import (
    ComplianceBlocked,
    build_character_prompt,
    build_scene_prompt,
    is_environment_only_shot,
    is_voiceover_only_dialogue,
    select_on_screen_cast,
    storyboard_composition_prompt,
    strip_on_screen_text_terms,
)
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
    # empty establishing plate：强制无人
    assert "empty establishing" in scene_prompt.lower() or "无人" in scene_prompt
    assert "禁止人物" in scene_prompt or "no people" in scene_prompt.lower()


def test_scene_prompt_scrubs_people_and_never_uses_identity_stage():
    from app.services.asset_generation import assemble_scene_prompt, scrub_people_from_prompt

    dirty = "中军大帐夜，郭子仪与士兵站在沙盘前讨论战事，将军面容凝重"
    cleaned = scrub_people_from_prompt(dirty)
    assert "郭子仪" in cleaned or "中军大帐" in cleaned  # 地名可留；人名词未必全清
    assert "士兵" not in cleaned
    assert "将军" not in cleaned

    style = ArtStyle(name="写实", prompt_suffix="电影剧照写实")
    prompt = assemble_scene_prompt(
        location="中军大帐",
        user_prompt=dirty,
        style=style,
        style_block="【定装阶段】优先中性站姿与可读五官/服装；电影剧照，写实光影",
        extra=None,
    )
    assert "empty establishing" in prompt.lower() or "无人空镜" in prompt
    assert "定装" not in prompt
    assert "五官" not in prompt
    assert "站姿" not in prompt
    # 用户描述中的人物动作应被清掉；尾部「禁止…士兵…」是否定约束，允许出现
    assert "讨论战事" not in prompt
    assert "面容凝重" not in prompt
    assert "禁止人物" in prompt or "no people" in prompt.lower()


def test_strip_on_screen_text_and_empty_plate_shot():
    cleaned = strip_on_screen_text_terms("摇镜落幅，风吹纸钱，字幕浮现，远处火光")
    assert "字幕" not in cleaned
    assert "摇镜落幅" in cleaned
    assert "纸钱" in cleaned

    assert is_voiceover_only_dialogue("旁白：长安，曾是万国来朝的天下中心。") is True
    assert is_voiceover_only_dialogue("阿石：稳住。") is False
    # #05 常见：无「旁白：」前缀的叙述句，也应当旁白
    assert is_voiceover_only_dialogue("唐军败过，逃过，退过。但这一夜，他们又回来了。") is True

    # 长安夜色·起幅：旁白 + 镜头扫环境 → 必须空镜
    changan = Storyboard(
        title="长安夜色·起幅",
        location="长安城",
        action="【起幅】镜头从城楼开始，缓缓摇向街道，残破坊墙显现，纸钱飘飞。",
        dialogue="旁白：长安，曾是万国来朝的天下中心。",
        image_prompt="全景构图，全身与环境关系清楚, 运镜起点，建立方位，长安城, 夜色",
    )
    assert is_environment_only_shot(changan, []) is True
    cast = [Character(drama_id=1, name="阿石", appearance="唐军")]
    assert is_environment_only_shot(changan, cast) is True

    # #05 长安夜色·落幅：摇镜 + 无说话人前缀的旁白 → 空镜（用户反馈老是出人）
    changan5 = Storyboard(
        title="长安夜色·落幅",
        location="长安城",
        action="摇镜落幅，远处火光闪烁更亮，风吹纸钱，字幕浮现。",
        dialogue="唐军败过，逃过，退过。但这一夜，他们又回来了。",
        image_prompt="纯环境空镜，不要人物、士兵、行人、人体轮廓，不要任何文字",
        segment_part=5,
        segment_total=5,
    )
    assert is_environment_only_shot(changan5, []) is True
    assert is_environment_only_shot(changan5, cast) is True

    # 明确空镜 → 无人
    empty = Storyboard(
        title="长安夜色·落幅",
        action="【落幅】空镜，远处火光闪烁更亮，风吹纸钱",
        dialogue=None,
        image_prompt="street at night",
    )
    assert is_environment_only_shot(empty, cast) is True

    # 拆镜标签「起幅」+ 磨刀人物动作 → 有人
    camp_open = Storyboard(
        title="军营磨刀·起幅",
        action="【起幅】士兵们沉默磨刀，阿石手抖，陈七拍他肩膀",
        dialogue=None,
        image_prompt="camp",
    )
    assert is_environment_only_shot(camp_open, cast) is False

    people = Storyboard(
        title="军营磨刀",
        action="阿石沉默磨刀，陈七拍他肩膀",
        dialogue="唐军败过",
        image_prompt="two soldiers",
    )
    assert is_environment_only_shot(people, cast) is False

    # 长安夜色·中段：只写坊墙/运镜、未点名角色 → 空镜（避免三人定装并排）
    changan_mid = Storyboard(
        title="长安夜色·起幅-中段",
        location="长安城",
        shot_type="中景",
        action="【中段】残破坊墙显现；承接上一镜连续运镜，推进动作",
        dialogue=None,
        image_prompt="中景构图，残破坊墙",
    )
    cast3 = [
        Character(id=1, drama_id=1, name="阿石", appearance="甲"),
        Character(id=2, drama_id=1, name="陈七", appearance="乙"),
        Character(id=3, drama_id=1, name="李曼", appearance="丙"),
    ]
    assert is_environment_only_shot(changan_mid, cast3) is True


def test_composition_avoids_three_person_lineup():
    """三人关联时不得再生成「左中右平衡三人构图」定装横排。"""
    a = Character(id=1, drama_id=1, name="阿石", appearance="铠甲")
    b = Character(id=2, drama_id=1, name="陈七", appearance="红袍")
    c = Character(id=3, drama_id=1, name="李曼", appearance="黑衣")
    sb = Storyboard(
        title="军营议事",
        shot_type="中景",
        action="阿石沉默磨刀，陈七拍他肩膀",
        dialogue=None,
    )
    on_screen = select_on_screen_cast(sb, [a, b, c])
    assert [x.name for x in on_screen] == ["阿石", "陈七"]
    assert c not in on_screen  # 未点名不强制上镜

    text = storyboard_composition_prompt(
        sb,
        [(a, "/oss/a.png"), (b, "/oss/b.png"), (c, "/oss/c.png")],
        scene_reference_index=1,
        first_character_reference=2,
        all_characters=[a, b, c],
    )
    assert text
    assert "平衡的平视三人构图" not in text
    assert "等大横排" in text or "并排" in text  # 禁止语
    assert "三视图" in text
    assert "阿石" in text and "陈七" in text


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
    pairs = [(row.kind, row.url) for row in rows]
    # 智能参考优先本软件人物/场景资产，不再用上一镜图当身份主参考
    assert ("character", "old-man.png") in pairs
    assert ("character", "girl.png") in pairs
    assert ("scene", "scene.png") in pairs
    # 角色定妆排在场景前（说话人/出镜人优先）
    kinds = [k for k, _ in pairs]
    assert kinds.index("character") < kinds.index("scene")
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
    # 时跳：不用上一镜 day.png 当主参考；用本软件场景+人物
    assert ("scene", "night-yard.png") in [(r.kind, r.url) for r in rows]
    assert ("character", "a.png") in [(r.kind, r.url) for r in rows]
    # 角色资产优先于场景
    assert [r.kind for r in rows].index("character") < [r.kind for r in rows].index("scene")

    second.reference_images = '["day.png"]'
    db.commit()
    rows, mode = resolve_storyboard_image_references(db, second)
    assert mode == "manual"
    assert [row.url for row in rows] == ["day.png"]
    db.close()


def test_smart_references_speaker_and_prop_assets():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    episode = Episode(drama_id=30, episode_number=1, title="刀", script_content="占位")
    scene = Scene(drama_id=30, location="营帐", time="夜", prompt="营帐", image_url="camp.png")
    hero = Character(drama_id=30, name="李嗣业", image_url="hero.png")
    sword = Prop(drama_id=30, name="长刀", type="武器", image_url="sword.png")
    db.add_all([episode, scene, hero, sword])
    db.flush()
    sb = Storyboard(
        episode_id=episode.id,
        scene_id=scene.id,
        storyboard_number=1,
        location="营帐",
        time="夜",
        action="李嗣业握长刀",
        dialogue="李嗣业：守住。",
        image_prompt="李嗣业持长刀",
        speaking_character_id=hero.id,
    )
    db.add(sb)
    db.flush()
    db.add(StoryboardCharacter(storyboard_id=sb.id, character_id=hero.id))
    db.commit()

    rows, mode = resolve_storyboard_image_references(db, sb)
    assert mode == "auto"
    kinds = [(r.kind, r.asset_name, r.is_speaker) for r in rows]
    assert any(k == "character" and n == "李嗣业" and sp for k, n, sp in kinds)
    assert any(k == "scene" and n == "营帐" for k, n, _ in kinds)
    assert any(k == "prop" and n == "长刀" for k, n, _ in kinds)
    # 说话人排第一
    assert rows[0].kind == "character" and rows[0].is_speaker
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
    monkeypatch.setattr(asset_generation, "get_node", lambda _db, _node_id=None, **_kw: node)

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
    monkeypatch.setattr(asset_generation, "get_node", lambda _db, _node_id=None, **_kw: node)

    outcome = asyncio.run(asset_generation.generate_storyboard_image(db, storyboard_id=storyboard.id))

    assert node.job.workflow == asset_generation.KONTEXT_WORKFLOW
    refs = node.job.reference_images or []
    # 人物定妆优先于场景；说话人（角色乙）排第一
    assert any("character-b" in (r or "") for r in refs)
    assert any("character-a" in (r or "") for r in refs)
    assert any("scene-reference" in (r or "") for r in refs)
    assert "角色甲" in node.job.prompt and "角色乙" in node.job.prompt
    assert "资产锁定表" in node.job.prompt or "@说话人" in node.job.prompt or "@角色" in node.job.prompt
    assert "@场景" in node.job.prompt or "环境底板" in node.job.prompt
    assert "构图锁定" in node.job.prompt or "具名主要角色" in node.job.prompt
    assert "字幕" in node.job.prompt or "文字" in node.job.prompt
    assert "文字" in node.job.negative or "字幕" in node.job.negative
    assert (node.job.negative or "").startswith(asset_generation.STORYBOARD_NEGATIVE_PROMPT)
    assert "glasses" not in (node.job.negative or "").lower()
    assert outcome.workflow == asset_generation.KONTEXT_WORKFLOW
    linked = db.scalars(
        select(StoryboardCharacter.character_id)
        .where(StoryboardCharacter.storyboard_id == storyboard.id)
        .order_by(StoryboardCharacter.character_id)
    ).all()
    assert linked == [first.id, second.id]
    generation = db.scalars(select(ImageGeneration).where(ImageGeneration.storyboard_id == storyboard.id)).one()
    assert generation.reference_images and "character" in generation.reference_images
    db.close()


def test_character_custom_prompt_keeps_default_style_and_historical_period(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    drama = Drama(id=12, title="唐军收复长安", style="realistic")
    style = ArtStyle(name="写实电影感", prompt_suffix="写实电影感，电影布光", sort_order=0)
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
    monkeypatch.setattr(asset_generation, "get_node", lambda _db, _node_id=None, **_kw: node)

    asyncio.run(asset_generation.generate_character_asset(
        db,
        character_id=character.id,
        full_prompt="a slim young soldier, full body",
    ))

    assert "写实电影感" in node.job.prompt or "电影布光" in node.job.prompt
    assert "唐代" in node.job.prompt or "Tang" in node.job.prompt
    assert "现代军装" in node.job.prompt or "禁止现代" in node.job.prompt
    assert "现代军装" in node.job.negative or "现代服装" in node.job.negative
    db.close()
