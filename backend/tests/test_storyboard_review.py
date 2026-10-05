from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.api.storyboard import update_storyboard
from app.models.domain import Episode, Scene, Storyboard, StoryboardReview
from app.schemas.storyboard import StoryboardUpdate
from app.services import storyboard_review


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_structural_review_flags_dialogue_and_repeated_shot_size():
    rows = [
        Storyboard(storyboard_number=index, duration=2, shot_type="近景", angle="平视", movement="固定", action="人物抬头", dialogue="被改写的台词")
        for index in range(1, 4)
    ]

    issues = storyboard_review.structural_issues(rows, "剧本原文台词", [])

    assert sum(item["category"] == "台词完整性" for item in issues) == 3
    assert any(item["category"] == "景别视角错开" for item in issues)


def test_dialogue_match_allows_punctuation_and_speaker_prefix():
    assert storyboard_review.dialogue_in_script("甲：你好世界", "乙走过来说：你好，世界！")
    assert storyboard_review.dialogue_in_script("你好世界", "剧本里写着你好世界对吧")
    assert not storyboard_review.dialogue_in_script("完全无关的句子", "剧本原文台词")


def test_scene_location_partial_match():
    names = {"城门", "内殿大堂"}
    assert storyboard_review.scene_location_matched("城门口", names)
    assert storyboard_review.scene_location_matched("内殿", names)
    assert not storyboard_review.scene_location_matched("后花园", names)
    assert storyboard_review.best_scene_location("城门口石阶", names) == "城门"


def test_grade_matches_supervisor_rules():
    grade, counts = storyboard_review.grade_issues([
        {"severity": "medium"},
        {"severity": "medium"},
        {"severity": "medium"},
    ])

    assert grade == "B"
    assert counts == {"severe": 0, "medium": 3, "minor": 0}


def test_user_can_edit_full_storyboard_after_agent_changes():
    db = make_db()
    episode = Episode(drama_id=2, episode_number=1, title="第一集")
    db.add(episode)
    db.flush()
    row = Storyboard(episode_id=episode.id, storyboard_number=1, duration=5, action="旧动作")
    db.add(row)
    db.commit()

    result = update_storyboard(
        row.id,
        StoryboardUpdate(action="用户修改后的动作", dialogue="用户确认的台词", shot_type="特写", duration=4),
        db,
    )

    assert result["action"] == "用户修改后的动作"
    assert result["dialogue"] == "用户确认的台词"
    assert result["shot_type"] == "特写"
    assert result["duration"] == 4


def test_agent_review_is_saved_and_can_be_loaded(monkeypatch):
    db = make_db()
    episode = Episode(drama_id=7, episode_number=1, title="第一集", script_content="甲：原文台词")
    db.add(episode)
    db.flush()
    db.add(Scene(drama_id=7, episode_id=episode.id, location="城门", time="夜", prompt="城门"))
    db.add(Storyboard(
        episode_id=episode.id, storyboard_number=1, title="城门对话", location="城门", time="夜",
        shot_type="近景", angle="平视", movement="固定", action="甲抬头", dialogue="甲：原文台词", duration=5,
    ))
    db.commit()

    monkeypatch.setattr(storyboard_review, "resolve_llm", lambda _db: ("https://llm.example", "placeholder", "review-model"))
    monkeypatch.setattr(
        storyboard_review,
        "chat_text",
        lambda *args, **kwargs: json.dumps({
            "summary": "整体可用，但需补一个反应镜头。",
            "issues": [{
                "severity": "medium", "category": "在场人物不消失", "storyboard_numbers": [1],
                "problem": "配角缺少视觉落点。", "suggestion": "加入虚焦反应。",
            }],
            "decisions": [],
        }, ensure_ascii=False),
    )

    report = storyboard_review.run_storyboard_review(db, episode.id)
    latest = storyboard_review.latest_storyboard_review(db, episode.id)

    assert report["grade"] == "A"
    assert report["model"] == "review-model"
    assert report["issues"][0]["category"] == "在场人物不消失"
    assert latest and latest["id"] == report["id"]


def test_deterministic_fixes_clamp_duration_and_fill_fields():
    rows = [
        Storyboard(storyboard_number=1, duration=12, shot_type="", angle="", movement="", action="", location="城门口", dialogue=""),
        Storyboard(storyboard_number=2, duration=5, shot_type="近景", angle="平视", movement="固定", action="站着", location="城门"),
        Storyboard(storyboard_number=3, duration=5, shot_type="近景", angle="平视", movement="固定", action="站着", location="城门"),
        Storyboard(storyboard_number=4, duration=5, shot_type="近景", angle="平视", movement="固定", action="站着", location="城门"),
    ]
    scenes = [Scene(drama_id=1, location="城门", time="日", prompt="x")]

    changes = storyboard_review.apply_deterministic_fixes(rows, "", scenes)

    assert rows[0].duration == 5
    assert rows[0].shot_type == "中景"
    assert rows[0].action
    assert rows[0].location == "城门"
    # 连续三镜同景别 → 中间一镜被打散
    assert rows[2].shot_type != "近景" or rows[1].shot_type != "近景" or rows[3].shot_type != "近景"
    assert changes


def test_one_click_remediation_applies_only_allowed_fields_and_keeps_diff(monkeypatch):
    db = make_db()
    episode = Episode(drama_id=8, episode_number=1, title="第一集", script_content="甲：原文台词")
    db.add(episode)
    db.flush()
    storyboard = Storyboard(
        episode_id=episode.id, storyboard_number=1, title="旧标题", location="城门", duration=5,
        shot_type="近景", angle="平视", movement="固定", action="甲站着", dialogue="甲：原文台词",
    )
    db.add(storyboard)
    db.flush()
    review = StoryboardReview(
        episode_id=episode.id, grade="C", summary="需要整改", severe_count=1,
        report_json=json.dumps({
            "issues": [{"id": 1, "severity": "severe", "category": "动作", "storyboard_numbers": [1], "problem": "动作不具体", "suggestion": "补动作链"}],
            "decisions": [],
        }, ensure_ascii=False),
    )
    db.add(review)
    db.commit()

    monkeypatch.setattr(storyboard_review, "resolve_llm", lambda _db: ("https://llm.example", "placeholder", "fix-model"))
    monkeypatch.setattr(
        storyboard_review,
        "chat_text",
        lambda *args, **kwargs: json.dumps({
            "summary": "补充动作并压缩时长。",
            "updates": [{
                "storyboard_number": 1,
                "changes": {"action": "甲抬头后握紧刀柄", "duration": 4, "storyboard_number": 99},
            }],
            "unresolved": [],
        }, ensure_ascii=False),
    )

    result = storyboard_review.remediate_storyboard_review(db, review.id)
    db.refresh(storyboard)
    db.refresh(review)

    assert result["changed_count"] == 1
    assert storyboard.action == "甲抬头后握紧刀柄"
    assert storyboard.duration == 4
    assert storyboard.storyboard_number == 1
    change = result["review"]["remediation"]["changes"][0]
    assert change["before"]["action"] == "甲站着"
    assert change["after"]["duration"] == 4
    # 整改后问题清单应被硬规则重验结果替换（此处应清空）
    assert result["residual_count"] == 0
    assert result["review"]["issues"] == []
    assert review.grade == "A"
    assert "[已整改]" in review.summary


def test_remediation_updates_residual_structural_issues(monkeypatch):
    """改完后若仍有硬规则问题，清单应显示剩余而非旧问题。"""
    db = make_db()
    episode = Episode(drama_id=9, episode_number=1, title="第一集", script_content="短句")
    db.add(episode)
    db.flush()
    # 超长台词：即使 duration=5 仍报「需拆镜」
    long_line = "这是一句非常非常非常非常非常非常非常非常非常非常非常长的台词需要拆镜"
    storyboard = Storyboard(
        episode_id=episode.id, storyboard_number=1, location="城门", duration=8,
        shot_type="近景", angle="平视", movement="固定", action="说话", dialogue=long_line,
    )
    db.add(storyboard)
    db.flush()
    review = StoryboardReview(
        episode_id=episode.id, grade="D", summary="旧报告", severe_count=2,
        report_json=json.dumps({
            "issues": [
                {"id": 1, "severity": "severe", "category": "片段时长", "storyboard_numbers": [1], "problem": "超 5 秒", "suggestion": "拆镜"},
                {"id": 2, "severity": "medium", "category": "台词时长", "storyboard_numbers": [1], "problem": "太长", "suggestion": "拆镜"},
            ],
            "decisions": [],
        }, ensure_ascii=False),
    )
    db.add(review)
    db.commit()

    monkeypatch.setattr(storyboard_review, "resolve_llm", lambda _db: ("https://llm.example", "placeholder", "fix-model"))
    monkeypatch.setattr(
        storyboard_review,
        "chat_text",
        lambda *args, **kwargs: json.dumps({"summary": "只能压时长", "updates": [], "unresolved": ["需拆镜"]}, ensure_ascii=False),
    )

    result = storyboard_review.remediate_storyboard_review(db, review.id)
    db.refresh(storyboard)

    assert storyboard.duration == 5  # 确定性压到 5
    assert result["changed_count"] >= 1
    assert result["residual_count"] >= 1
    assert any(i["category"] == "台词时长" for i in result["review"]["issues"])
    assert result["review"]["remediation"]["needs_semantic_rereview"] is True
