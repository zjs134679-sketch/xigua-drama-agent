"""小白一键出片：bootstrap / 转场映射 / 程序化 BGM 路径（无外部版权素材）。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import Drama, Episode
from app.services.bgm_synth import list_moods, resolve_mood
from app.services.easy_pipeline import bootstrap_from_idea
from app.services.video_compose import _map_transition, compose_video


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_moods_are_original_presets():
    moods = list_moods()
    ids = {m["id"] for m in moods}
    assert "warm" in ids
    assert "tense" in ids
    assert resolve_mood("unknown") == "warm"
    assert resolve_mood("epic") == "epic"


def test_transition_map_only_ffmpeg_builtins():
    assert _map_transition("none") is None
    assert _map_transition("fade") == "fade"
    assert _map_transition("fadeblack") == "fadeblack"
    assert _map_transition("not-a-real-effect") == "fade"


def test_bootstrap_short_text_becomes_script():
    db = make_db()
    out = bootstrap_from_idea(
        db,
        title="测试剧",
        text="男主重生回到三年前，决定改写命运，先人一步布局商业版图。",
        username="tester",
    )
    assert out["drama_id"]
    assert out["episode_id"]
    assert out["has_script"] is True
    ep = db.get(Episode, out["episode_id"])
    assert ep is not None
    assert ep.script_content and "重生" in ep.script_content
    drama = db.get(Drama, out["drama_id"])
    assert drama is not None
    assert drama.title == "测试剧"
    db.close()


def test_bootstrap_rejects_too_short():
    db = make_db()
    try:
        bootstrap_from_idea(db, title="x", text="太短了")
        assert False, "should raise"
    except ValueError as exc:
        assert "太短" in str(exc)
    db.close()


def test_compose_with_fade_builds_xfade_filter(tmp_path: Path):
    """不真跑 ffmpeg：用 mock runner 检查命令含 xfade。"""
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"fake")
    v2.write_bytes(b"fake")

    commands: list[list[str]] = []

    def fake_runner(cmd, **kwargs):
        commands.append(cmd)
        # 写出“产物”让后续 copy 不炸：compose 会写到 temp 再可能失败
        # 我们只断言第一条 filter 含 xfade，并在 runner 里 touch 输出文件
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    timeline = {
        "episode_id": 1,
        "duration": 6,
        "transition": "fade",
        "transition_duration": 0.3,
        "tracks": {
            "video": {
                "enabled": True,
                "clips": [
                    {"index": 0, "start": 0, "duration": 3, "video_url": str(v1), "storyboard_id": 1},
                    {"index": 1, "start": 3, "duration": 3, "video_url": str(v2), "storyboard_id": 2},
                ],
            },
            "voiceover": {"enabled": False, "clips": []},
            "subtitle": {"enabled": False, "clips": []},
            "music": {"enabled": False, "clips": []},
        },
    }
    result = compose_video(timeline, tmp_path / "out", runner=fake_runner, ffmpeg_path="ffmpeg")
    assert result.output_path.exists()
    joined = " ".join(" ".join(c) for c in commands)
    assert "xfade" in joined
    assert "fade" in joined
