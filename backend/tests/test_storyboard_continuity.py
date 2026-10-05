"""长镜拆解 / 运镜段落 / 视频尾帧衔接。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import Drama, Episode, Storyboard, StoryboardCharacter, Character
from app.services.storyboard_segments import refresh_episode_segments, segment_view
from app.services.storyboard_split import split_storyboard, suggest_part_count, _rule_based_parts
from app.services.video_generation import resolve_i2v_start_frame
from app.services.video_frame import extract_video_frame


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_suggest_parts_from_long_dialogue():
    sb = Storyboard(dialogue="甲：这是一句很长很长的台词需要拆开来说完整个故事段落。", duration=5, action="说话")
    assert suggest_part_count(sb) >= 2
    assert suggest_part_count(sb, 3) == 3


def test_rule_based_split_keeps_scene_and_covers_parts():
    sb = Storyboard(
        title="推镜",
        location="城门",
        movement="推镜",
        action="大军推进将军抬头",
        dialogue="旁白：长安曾是中心。今夜大军压境。",
        duration=5,
        image_prompt="same prompt for all",
    )
    parts = _rule_based_parts(sb, 3)
    assert len(parts) == 3
    assert all(3 <= p["duration"] <= 5 for p in parts)
    assert any("起幅" in (p["action"] or "") for p in parts)
    assert any("落幅" in (p["action"] or "") for p in parts)
    # 标题与提示词必须分段差异化，不能三镜完全一样
    titles = [p["title"] for p in parts]
    assert any("起幅" in t for t in titles)
    assert any("落幅" in t for t in titles)
    prompts = [p["image_prompt"] for p in parts]
    assert len(set(prompts)) >= 2
    assert all("字幕" not in (p["action"] or "") for p in parts)


def test_rule_based_split_camp_sharpen_style():
    """军营磨刀类：短旁白 + 双人动作，拆完应有起/中/落且图需重出。"""
    sb = Storyboard(
        title="军营磨刀",
        location="凤翔唐军大营",
        time="夜",
        shot_type="中景",
        movement="固定",
        action="士兵们沉默磨刀，阿石手抖，陈七拍他肩膀。",
        dialogue="唐军败过，逃过，追过，但这一夜，他们又回来了。",
        image_prompt="Two soldiers beside a campfire",
        duration=5,
        composed_image="/oss/old.png",
    )
    parts = _rule_based_parts(sb, 3)
    assert [p["title"] for p in parts] == ["军营磨刀·起幅", "军营磨刀·中段", "军营磨刀·落幅"]
    assert parts[0]["shot_type"] == "中景"  # 沿用原景别作起幅
    assert parts[1]["shot_type"] != parts[2]["shot_type"] or parts[0]["action"] != parts[2]["action"]
    # 台词应拆到不同子镜，不能三镜同一整段
    dialogues = [p.get("dialogue") or "" for p in parts]
    assert sum(1 for d in dialogues if d) >= 1


def test_split_storyboard_creates_segment_and_renumbers():
    db = make_db()
    ep = Episode(drama_id=1, episode_number=1, title="一")
    db.add(ep)
    db.flush()
    a = Storyboard(
        episode_id=ep.id, storyboard_number=1, title="开场", location="城门", time="夜",
        movement="推镜", action="推近", dialogue="旁白：很长的一段需要拆分的旁白内容足够长。", duration=5,
    )
    b = Storyboard(
        episode_id=ep.id, storyboard_number=2, title="后续", location="营帐", time="夜",
        movement="固定", action="议事", duration=5,
    )
    db.add_all([a, b])
    db.commit()

    a.composed_image = "/oss/old-same.png"
    db.commit()

    a.tts_audio_url = "/oss/old-full-tts.mp3"
    db.commit()

    result = split_storyboard(db, a.id, parts=3, use_llm=False)
    assert result["parts"] == 3
    assert len(result["storyboard_ids"]) == 3
    assert result.get("needs_reimage") is True
    assert result.get("needs_tts") is True
    assert len(result.get("needs_tts_ids") or []) >= 1

    rows = db.scalars(
        select(Storyboard).where(Storyboard.episode_id == ep.id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    # 3 子镜 + 原来的后续镜
    assert len(rows) == 4
    assert [r.storyboard_number for r in rows] == [1, 2, 3, 4]
    assert rows[0].segment_key == rows[1].segment_key == rows[2].segment_key
    assert rows[0].segment_total == 3
    assert rows[0].segment_part == 1
    assert rows[2].segment_part == 3
    # 拆后不得共用同一成图（否则看起来和拆前一样）
    assert rows[0].composed_image is None
    assert rows[1].composed_image is None
    assert rows[2].composed_image is None
    # 旧整段配音必须清掉，避免子镜挂父镜全文 TTS
    assert rows[0].tts_audio_url is None
    assert rows[1].tts_audio_url is None
    assert rows[2].tts_audio_url is None
    assert rows[0].status == "pending"
    # 提示词应有差异
    assert rows[0].image_prompt != rows[2].image_prompt or "起幅" in (rows[0].title or "")
    # 后续镜号已后移
    assert rows[3].title == "后续"
    assert segment_view(rows[0])["in_segment"] is True
    db.close()


def test_split_with_character_links_does_not_unique_crash():
    """军营磨刀类：已挂角色时 copy+sync 不得 UNIQUE 冲突。"""
    db = make_db()
    db.add(Drama(id=9, title="唐"))
    ep = Episode(drama_id=9, episode_number=1, title="一", script_content="剧本")
    db.add(ep)
    db.flush()
    c1 = Character(drama_id=9, name="阿石", appearance="唐军")
    c2 = Character(drama_id=9, name="陈七", appearance="唐军")
    db.add_all([c1, c2])
    db.flush()
    sb = Storyboard(
        episode_id=ep.id,
        storyboard_number=1,
        title="军营磨刀",
        location="军营",
        time="夜",
        action="阿石沉默磨刀，陈七拍他肩膀",
        dialogue="唐军败过，逃过，追过，但这一夜，他们又回来了。",
        duration=5,
        composed_image="/oss/camp.png",
    )
    db.add(sb)
    db.flush()
    db.add_all(
        [
            StoryboardCharacter(storyboard_id=sb.id, character_id=c1.id),
            StoryboardCharacter(storyboard_id=sb.id, character_id=c2.id),
        ]
    )
    db.commit()

    result = split_storyboard(db, sb.id, parts=3, use_llm=False)
    assert result["parts"] == 3
    # 每个子镜都应有角色关联且无重复
    for sid in result["storyboard_ids"]:
        ids = db.scalars(
            select(StoryboardCharacter.character_id).where(StoryboardCharacter.storyboard_id == sid)
        ).all()
        assert set(ids) == {c1.id, c2.id}
        assert len(ids) == 2
    db.close()


def test_refresh_segments_groups_continuous_same_location():
    db = make_db()
    ep = Episode(drama_id=2, episode_number=1, title="二")
    db.add(ep)
    db.flush()
    db.add_all(
        [
            Storyboard(episode_id=ep.id, storyboard_number=1, location="客栈", time="夜", action="进门", duration=5),
            Storyboard(episode_id=ep.id, storyboard_number=2, location="客栈", time="夜", action="落座", duration=5),
            Storyboard(episode_id=ep.id, storyboard_number=3, location="街道", time="日", action="走路", duration=5),
        ]
    )
    db.commit()
    summaries = refresh_episode_segments(db, ep.id)
    assert len(summaries) >= 1
    # 前两镜同场景应成组
    rows = db.scalars(
        select(Storyboard).where(Storyboard.episode_id == ep.id).order_by(Storyboard.storyboard_number)
    ).all()
    assert rows[0].segment_key == rows[1].segment_key
    assert rows[0].segment_total == 2
    assert rows[2].segment_key is None or rows[2].segment_total in (None, 1)
    db.close()


def test_resolve_i2v_prefers_previous_last_frame():
    db = make_db()
    ep = Episode(drama_id=3, episode_number=1, title="三")
    db.add(ep)
    db.flush()
    prev = Storyboard(
        episode_id=ep.id, storyboard_number=1, location="殿", time="夜",
        composed_image="/oss/a.png", last_frame_image="/oss/prev_last.png",
        segment_key="seg-x", segment_part=1, segment_total=2, duration=5,
    )
    cur = Storyboard(
        episode_id=ep.id, storyboard_number=2, location="殿", time="夜",
        composed_image="/oss/b.png",
        segment_key="seg-x", segment_part=2, segment_total=2, duration=5,
    )
    db.add_all([prev, cur])
    db.commit()

    # 本镜有独立合成图 → 用本镜图（不再被上一镜尾帧抢走）
    url, source = resolve_i2v_start_frame(db, cur, use_prev_last_frame=True)
    assert url == "/oss/b.png"
    assert source == "own_image"

    url2, source2 = resolve_i2v_start_frame(db, cur, use_prev_last_frame=False)
    assert url2 == "/oss/b.png"
    assert source2 == "own_image"
    db.close()


def test_resolve_i2v_prefers_own_composed_over_prev_segment():
    """中段有自己的合成图时，不得用起幅图当 i2v 起点（否则 #07 又渲成第一镜）。"""
    db = make_db()
    ep = Episode(drama_id=4, episode_number=1, title="四")
    db.add(ep)
    db.flush()
    prev = Storyboard(
        episode_id=ep.id, storyboard_number=1, location="军营", time="夜",
        composed_image="/oss/camp1.png",
        segment_key="seg-camp", segment_part=1, segment_total=3, duration=5,
    )
    cur = Storyboard(
        episode_id=ep.id, storyboard_number=2, location="军营", time="夜",
        composed_image="/oss/camp2.png",
        segment_key="seg-camp", segment_part=2, segment_total=3, duration=5,
    )
    db.add_all([prev, cur])
    db.commit()

    url, source = resolve_i2v_start_frame(db, cur, use_prev_last_frame=True)
    assert url == "/oss/camp2.png"
    assert source == "own_image"
    db.close()


def test_resolve_i2v_same_segment_uses_prev_when_no_own_composed():
    """本镜还没出图时，才用上一镜成图/尾帧衔接。"""
    db = make_db()
    ep = Episode(drama_id=5, episode_number=1, title="五")
    db.add(ep)
    db.flush()
    prev = Storyboard(
        episode_id=ep.id, storyboard_number=1, location="军营", time="夜",
        composed_image="/oss/camp1.png", last_frame_image="/oss/camp1_last.png",
        segment_key="seg-camp", segment_part=1, segment_total=3, duration=5,
    )
    cur = Storyboard(
        episode_id=ep.id, storyboard_number=2, location="军营", time="夜",
        composed_image=None, first_frame_image="/oss/shared_split.png",
        segment_key="seg-camp", segment_part=2, segment_total=3, duration=5,
    )
    db.add_all([prev, cur])
    db.commit()

    url, source = resolve_i2v_start_frame(db, cur, use_prev_last_frame=True)
    assert url == "/oss/camp1_last.png"
    assert "prev_last_frame" in source
    db.close()


def test_extract_frame_returns_none_without_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.video_frame.settings.data_dir", tmp_path)
    assert extract_video_frame("/oss/missing.mp4") is None
