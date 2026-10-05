"""负向配置表 + 时长对齐 + 提示编排回退。"""
from __future__ import annotations

from app.services.duration_align import align_shot_dict, clamp_shot_duration
from app.services.negative_packs import (
    character_negative,
    load_packs,
    pack_enabled,
    protection_prompt,
    storyboard_negative,
    video_negative,
)
from app.services.prompt_orchestrate import align_video_prompt_to_duration


def test_negative_packs_load():
    data = load_packs(force=True)
    assert data.get("version") == 1
    assert "文字" in protection_prompt() or "水印" in protection_prompt()
    gen = character_negative()
    assert "畸形手" in gen or "多余的手" in gen


def test_genre_disables_military_pack():
    assert pack_enabled("character_no_modern_military", genre="古装玄幻") is True
    assert pack_enabled("character_no_modern_military", genre="都市现代") is False
    modern = character_negative(genre="都市", narrative="现代商战")
    ancient = character_negative(genre="古装", narrative="权谋")
    # 古装默认带军事禁令词；都市题材关闭该 pack
    assert ("迷彩" in ancient) or ("枪械" in ancient) or ("军装" in ancient)
    assert "迷彩" not in modern and "枪械" not in modern


def test_storyboard_negative_empty_has_people_ban():
    empty = storyboard_negative(empty_plate=True)
    people = storyboard_negative(empty_plate=False)
    assert "字幕" in empty or "文字" in empty
    assert len(empty) >= len(people) * 0.5


def test_video_negative_variants():
    a = video_negative(empty_plate=True)
    b = video_negative(empty_plate=False)
    assert "subtitle" in a.lower() or "字幕" in a
    assert a != b or "person" in a.lower()


def test_clamp_and_align_video_prompt():
    assert clamp_shot_duration(12) == 5
    assert clamp_shot_duration(1) == 3
    aligned = align_video_prompt_to_duration(
        "0-2秒：推门。2-5秒：进门环视。5-8秒：坐下说话。",
        3,
    )
    assert "5-8" not in aligned and "8秒" not in aligned
    assert "0-2" in aligned or "0-3" in aligned
    shot = align_shot_dict(
        {
            "duration": 12,
            "video_prompt": "0-10秒：长镜头缓慢推进，人物走完全程。",
            "image_prompt": "0-5秒：女主站在门口，侧光。",
        }
    )
    assert shot["duration"] == 5
    assert "10秒" not in (shot["video_prompt"] or "")
    # 静帧去掉时间片
    assert "0-5秒" not in (shot["image_prompt"] or "")
