"""分镜风格审核硬规则。"""
from __future__ import annotations

from app.models.domain import Storyboard
from app.services.storyboard_review import style_prompt_issues


def test_style_prompt_missing_anchors_and_bans():
    shots = [
        Storyboard(
            episode_id=1,
            storyboard_number=1,
            image_prompt="夜晚军营，士兵蹲在火边",
            video_prompt="",
            atmosphere="",
            action="蹲坐",
        ),
        Storyboard(
            episode_id=1,
            storyboard_number=2,
            image_prompt="电影剧照, 写实光影, 统一色调，带水印的海报风",
            atmosphere="压抑",
            action="对峙",
        ),
    ]
    issues = style_prompt_issues(
        shots,
        must_include=["电影剧照", "写实光影", "统一色调", "景深层次"],
        must_exclude=["水印", "字幕条"],
    )
    cats = {i["category"] for i in issues}
    assert "风格锚词" in cats or "风格禁用" in cats
    ban_hit = [i for i in issues if i["category"] == "风格禁用"]
    assert ban_hit
    assert 2 in ban_hit[0]["storyboard_numbers"]
