from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import timeline as timeline_api
from fastapi import HTTPException

from app.api.timeline import ExportRequest, TTSRequest, build_timeline, get_timeline_document
from app.core.db import Base
from app.models.domain import Character, Episode, Storyboard, VideoMerge
from app.models.system import Violation
from app.services.compliance import enforce
from app.services.compliance.filter import FilterResult, Hit
from app.services.video_compose import compose_video


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def seed_episode(db: Session) -> Episode:
    episode = Episode(drama_id=1, episode_number=1, title="占位分集")
    db.add(episode)
    db.flush()
    db.add_all(
        [
            Storyboard(
                episode_id=episode.id,
                storyboard_number=2,
                title="镜头二",
                duration=5,
                video_url="video-2.mp4",
                dialogue="第二句字幕",
                tts_audio_url="voice-2.wav",
            ),
            Storyboard(
                episode_id=episode.id,
                storyboard_number=1,
                title="镜头一",
                duration=3,
                composed_video_url="video-1.mp4",
                dialogue="第一句字幕",
            ),
        ]
    )
    db.commit()
    return episode


def test_timeline_is_assembled_in_storyboard_order_with_accumulated_start():
    db = make_db()
    episode = seed_episode(db)

    timeline = build_timeline(db, episode.id)
    video = timeline.tracks.video.clips

    assert [clip.video_url for clip in video] == ["video-1.mp4", "video-2.mp4"]
    assert [clip.index for clip in video] == [0, 1]
    assert [clip.start for clip in video] == [0, 3]
    assert timeline.duration == 8
    # 已取消独立 TTS：配音轨为空，语音在视频原生音轨
    assert timeline.tracks.voiceover.clips == []
    assert [clip.subtitle_text for clip in timeline.tracks.subtitle.clips] == ["第一句字幕", "第二句字幕"]
    db.close()


def test_saved_timeline_hydrates_latest_video_url_from_storyboard():
    """缓存时间线若无 video_url，GET 时应从分镜表回填，成片台才能预览。"""
    db = make_db()
    episode = seed_episode(db)
    boards = db.scalars(
        select(Storyboard).where(Storyboard.episode_id == episode.id).order_by(Storyboard.storyboard_number)
    ).all()
    # 缓存里故意不带视频
    scenes = {
        "episode_id": episode.id,
        "duration": 8,
        "tracks": {
            "video": {
                "enabled": True,
                "clips": [
                    {
                        "storyboard_id": boards[0].id,
                        "index": 0,
                        "start": 0,
                        "duration": 3,
                        "video_url": None,
                        "thumbnail": None,
                    },
                    {
                        "storyboard_id": boards[1].id,
                        "index": 1,
                        "start": 3,
                        "duration": 5,
                        "video_url": None,
                    },
                ],
            },
            "voiceover": {"enabled": False, "clips": []},
            "subtitle": {"enabled": False, "clips": []},
            "music": {"enabled": False, "clips": []},
        },
    }
    db.add(
        VideoMerge(
            episode_id=episode.id,
            drama_id=episode.drama_id,
            provider="timeline",
            scenes=json.dumps(scenes, ensure_ascii=False),
            duration=8,
            status="draft",
        )
    )
    boards[0].video_url = "video-1.mp4"
    boards[1].video_url = "video-2.mp4"
    db.commit()

    doc = get_timeline_document(db, episode.id)
    assert [c.video_url for c in doc.tracks.video.clips] == ["video-1.mp4", "video-2.mp4"]
    db.close()


def test_saved_timeline_clips_over_5s_are_clamped_on_read():
    """成片台优先读 video_merges.scenes；历史 12s 必须在 GET 时钳到 5s。"""
    db = make_db()
    episode = seed_episode(db)
    boards = db.scalars(
        select(Storyboard).where(Storyboard.episode_id == episode.id).order_by(Storyboard.storyboard_number)
    ).all()
    scenes = {
        "episode_id": episode.id,
        "duration": 24,
        "tracks": {
            "video": {
                "enabled": True,
                "clips": [
                    {
                        "storyboard_id": boards[0].id,
                        "index": 0,
                        "start": 0,
                        "duration": 12,
                        "video_url": boards[0].composed_video_url,
                        "subtitle_text": boards[0].dialogue,
                    },
                    {
                        "storyboard_id": boards[1].id,
                        "index": 1,
                        "start": 12,
                        "duration": 12,
                        "video_url": boards[1].video_url,
                        "subtitle_text": boards[1].dialogue,
                    },
                ],
            },
            "voiceover": {"enabled": False, "clips": []},
            "subtitle": {"enabled": True, "clips": []},
            "music": {"enabled": False, "clips": []},
        },
    }
    db.add(
        VideoMerge(
            episode_id=episode.id,
            drama_id=episode.drama_id,
            provider="timeline",
            scenes=json.dumps(scenes, ensure_ascii=False),
            duration=24,
            status="draft",
        )
    )
    db.commit()

    doc = get_timeline_document(db, episode.id)
    # 超限 12s 会钳到 5s；若分镜表本身是 3s 则保留分镜时长
    durs = [c.duration for c in doc.tracks.video.clips]
    assert all(d <= 5.0 for d in durs)
    assert max(durs) <= 5.0
    assert doc.duration == sum(durs)
    db.close()


def test_tts_endpoints_are_gone():
    """独立 TTS 已移除，接口返回 410。"""
    from fastapi import HTTPException

    try:
        timeline_api.generate_storyboard_tts_removed(1)
        raise AssertionError("应 410")
    except HTTPException as exc:
        assert exc.status_code == 410
    try:
        timeline_api.generate_timeline_tts_removed(1)
        raise AssertionError("应 410")
    except HTTPException as exc:
        assert exc.status_code == 410


def test_export_blocks_placeholder_red_subtitle_and_records_mask(monkeypatch):
    db = make_db()
    episode = seed_episode(db)
    blocked = FilterResult("red", [Hit("占位命中词", "red", "测试")])
    monkeypatch.setattr(timeline_api, "check", lambda _text: blocked)
    monkeypatch.setattr(timeline_api, "ensure_active_user", lambda *_args: None)
    monkeypatch.setattr(enforce, "report_to_auth", lambda *_args: None)

    def should_not_compose(*_args, **_kwargs):
        raise AssertionError("被拦截的字幕不得进入导出")

    monkeypatch.setattr(timeline_api, "compose_video", should_not_compose)
    response = timeline_api.export_timeline(episode.id, ExportRequest(username="timeline-test"), db)
    payload = json.loads(response.body)

    assert response.status_code == 451
    assert payload["storyboard_id"] is not None
    violation = db.scalars(select(Violation)).one()
    assert violation.word == "占****"
    assert violation.word != blocked.hits[0].word
    assert violation.source == "timeline_subtitle"
    db.close()


def test_ffmpeg_commands_are_argument_lists(monkeypatch, tmp_path: Path):
    calls: list[tuple[list[str], dict]] = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).touch()

    timeline = {
        "episode_id": 7,
        "duration": 4,
        "tracks": {
            "video": {
                "enabled": True,
                "clips": [{"storyboard_id": 11, "index": 0, "start": 0, "duration": 4, "video_url": "clip.mp4"}],
            },
            "voiceover": {
                "enabled": True,
                "clips": [{"storyboard_id": 11, "index": 0, "start": 0, "duration": 4, "audio_url": "voice.wav"}],
            },
            "subtitle": {
                "enabled": True,
                "clips": [{"storyboard_id": 11, "index": 0, "start": 0, "duration": 4, "subtitle_text": "占位字幕"}],
            },
            "music": {"enabled": False, "clips": []},
        },
    }

    result = compose_video(timeline, tmp_path, runner=fake_runner, ffmpeg_path="ffmpeg-test")

    assert result.output_path.suffix == ".mp4"
    # 无独立 TTS 时：视频拼接（可能带音）+ 字幕，通常 2 步；失败回退再加 1
    assert 1 <= len(calls) <= 3
    assert all(isinstance(command, list) for command, _ in calls)
    assert all(all(isinstance(argument, str) for argument in command) for command, _ in calls)
    assert all("shell" not in kwargs for _, kwargs in calls)
    assert all(command[0] == "ffmpeg-test" for command, _ in calls)


def test_ffmpeg_applies_trim_in_out():
    """导出命令应带 -ss 入点与 -t 时长（trim_out - trim_in）。"""
    from app.services.video_compose import clip_trim_window

    assert clip_trim_window({"duration": 5, "trim_in": 1.0, "trim_out": 4.0}) == (1.0, 3.0)
    assert clip_trim_window({"duration": 5, "trim_in": 0.5})[0] == 0.5

    calls: list[list[str]] = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        Path(command[-1]).touch()

    timeline = {
        "episode_id": 9,
        "duration": 5,
        "tracks": {
            "video": {
                "enabled": True,
                "clips": [
                    {
                        "storyboard_id": 1,
                        "index": 0,
                        "start": 0,
                        "duration": 5,
                        "trim_in": 1.2,
                        "trim_out": 4.2,
                        "video_url": "a.mp4",
                    }
                ],
            },
            "voiceover": {"enabled": False, "clips": []},
            "subtitle": {"enabled": False, "clips": []},
            "music": {"enabled": False, "clips": []},
        },
    }
    result = compose_video(timeline, Path("."), runner=fake_runner, ffmpeg_path="ffmpeg-test")
    # 仅视频轨时：concat 一步后 copy 到输出（无字幕无音轨时 1 次 run + copy 不调用 runner）
    assert calls, "应至少调用一次 ffmpeg"
    cmd = calls[0]
    assert "-ss" in cmd
    assert "1.200" in cmd
    assert "-t" in cmd
    assert "3.000" in cmd  # 4.2 - 1.2
    assert abs(result.duration - 3.0) < 0.01
