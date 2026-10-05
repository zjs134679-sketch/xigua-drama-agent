"""成片提示词：空镜不去加「角色说话」，禁止多人定装诱导。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import Character, Episode, Storyboard, StoryboardCharacter
from app.services.video_generation import build_video_prompt_parts


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_empty_env_shot_video_prompt_forbids_people():
    db = make_db()
    ep = Episode(drama_id=1, episode_number=1, title="一", script_content="旁白")
    db.add(ep)
    db.flush()
    a = Character(drama_id=1, name="阿石", appearance="甲")
    db.add(a)
    db.flush()
    sb = Storyboard(
        episode_id=ep.id,
        storyboard_number=2,
        title="长安夜色·中段",
        location="长安城",
        shot_type="中景",
        action="【中段】残破坊墙显现；承接上一镜连续运镜",
        dialogue="旁白：长安曾是天下中心。",
        video_prompt="0-5秒：镜头缓摇，残破坊墙。",
        duration=5,
    )
    db.add(sb)
    db.flush()
    db.add(StoryboardCharacter(storyboard_id=sb.id, character_id=a.id))
    db.commit()

    full, negative, empty, has_speech = build_video_prompt_parts(
        db, sb, base_prompt=sb.video_prompt or "", frame_source="own_image"
    )
    assert empty is True
    assert has_speech is False  # 空镜不张嘴，但旁白要照念
    assert "角色正在说话" not in full
    assert "纯环境" in full or "不要出现任何人物" in full
    assert "no people" in full.lower()
    assert "长安" in full  # 旁白原文必须进提示词
    assert "必须照念" in full or "画外音" in full
    assert "二维码" in full or "qr code" in full.lower()
    assert "字幕" in full or "subtitle" in negative.lower()
    assert "qr" in negative.lower() or "二维码" in negative
    assert "person" in negative.lower()
    db.close()


def test_dialogue_shot_has_speaking_but_anti_crowd_and_no_script_text():
    db = make_db()
    ep = Episode(drama_id=1, episode_number=1, title="一", script_content="阿石：我不看天。")
    db.add(ep)
    db.flush()
    a = Character(drama_id=1, name="阿石", appearance="甲")
    db.add(a)
    db.flush()
    sb = Storyboard(
        episode_id=ep.id,
        storyboard_number=11,
        title="中军议战",
        action="阿石握刀说话",
        dialogue="阿石：我不看天。看明天谁先怕。",
        speaking_character_id=a.id,
        video_prompt="近景，阿石说话。台词：阿石：我不看天。看明天谁先怕。",
        duration=5,
    )
    db.add(sb)
    db.flush()
    db.add(StoryboardCharacter(storyboard_id=sb.id, character_id=a.id))
    db.commit()

    full, negative, empty, has_speech = build_video_prompt_parts(
        db, sb, base_prompt=sb.video_prompt or "", frame_source="own_image"
    )
    assert empty is False
    assert has_speech is True
    assert "说话" in full
    assert "阿石" in full
    # 产品策略：台词进入正向供视频模型生成语音（仍要求画面无字幕）
    assert "我不看天" in full
    assert "必须照念" in full
    assert "禁止改写" in full or "一字不改" in full
    assert "等大横排" in full or "不要新增" in full
    assert "字幕" in full  # 禁止画面字幕
    assert "subtitle" in negative.lower() or "字幕" in negative
    assert "qr" in negative.lower()
    assert "clear natural speech" not in full.lower()  # 禁止诱导即兴乱说
    db.close()


def test_no_dialogue_forces_silence_not_improv():
    """无台词镜头必须静音约束，不能写 clear natural speech。"""
    db = make_db()
    ep = Episode(drama_id=1, episode_number=1, title="一", script_content="")
    db.add(ep)
    db.flush()
    a = Character(drama_id=1, name="老周", appearance="邮差")
    db.add(a)
    db.flush()
    sb = Storyboard(
        episode_id=ep.id,
        storyboard_number=1,
        title="无对白动作",
        action="老周捻信封",
        dialogue="",
        video_prompt="特写，老周捻信封，纸页沙沙。",
        duration=3,
    )
    db.add(sb)
    db.flush()
    db.add(StoryboardCharacter(storyboard_id=sb.id, character_id=a.id))
    db.commit()

    full, _neg, empty, has_speech = build_video_prompt_parts(
        db, sb, base_prompt=sb.video_prompt or "", frame_source="own_image"
    )
    assert empty is False
    assert has_speech is False
    assert "无对白" in full or "闭嘴" in full
    assert "no dialogue" in full.lower() or "no speech" in full.lower()
    assert "clear natural speech" not in full.lower()
    db.close()


def test_strip_spoken_lines_for_video():
    from app.services.video_generation import strip_spoken_lines_for_video

    raw = "近景运镜。台词：阿石：我不看天。纸钱飘飞。"
    cleaned = strip_spoken_lines_for_video(raw)
    # 仅剥舞台括注，保留对白供模型生成语音
    assert "我不看天" in cleaned
    assert "近景" in cleaned or "纸钱" in cleaned


def test_extract_dialogue_from_video_prompt_when_dialogue_empty():
    from app.services.video_generation import extract_dialogue_from_shot_fields

    got = extract_dialogue_from_shot_fields(
        "",
        "0-3秒：老周抬头。老周：这信是写给我的？女孩递过纸箱。",
        "女孩：师傅，帮我寄一下。",
    )
    assert "老周：这信是写给我的？" in got
    assert "女孩：师傅，帮我寄一下。" in got
    # dialogue 优先
    assert extract_dialogue_from_shot_fields("阿石：稳住。", "阿石：别写这个") == "阿石：稳住。"


def test_infer_speaking_character_from_dialogue():
    from app.models.domain import Character, Drama, Episode, Storyboard
    from app.services.video_generation import infer_speaking_character_id

    db = make_db()
    drama = Drama(title="测", genre="都市")
    db.add(drama)
    db.flush()
    ep = Episode(drama_id=drama.id, episode_number=1, title="一", script_content="老周：信？")
    db.add(ep)
    db.flush()
    a = Character(drama_id=drama.id, name="老周", appearance="邮差")
    b = Character(drama_id=drama.id, name="女孩", appearance="米色外套")
    db.add_all([a, b])
    db.flush()
    sb = Storyboard(
        episode_id=ep.id,
        storyboard_number=1,
        dialogue="老周：这信是写给我的？",
        video_prompt="近景",
        duration=3,
    )
    db.add(sb)
    db.commit()
    assert infer_speaking_character_id(db, sb) == a.id
    db.close()
