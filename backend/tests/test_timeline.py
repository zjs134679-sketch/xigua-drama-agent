from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import timeline as timeline_api
from app.api.timeline import ExportRequest, build_timeline
from app.core.db import Base
from app.models.domain import Episode, Storyboard
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
    assert [clip.audio_url for clip in timeline.tracks.voiceover.clips] == ["voice-2.wav"]
    assert [clip.subtitle_text for clip in timeline.tracks.subtitle.clips] == ["第一句字幕", "第二句字幕"]
    db.close()


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
    assert len(calls) == 3
    assert all(isinstance(command, list) for command, _ in calls)
    assert all(all(isinstance(argument, str) for argument in command) for command, _ in calls)
    assert all("shell" not in kwargs for _, kwargs in calls)
    assert all(command[0] == "ffmpeg-test" for command, _ in calls)
