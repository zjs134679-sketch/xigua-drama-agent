from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.domain import Asset, Episode, Storyboard, VideoMerge
from app.services.compliance import check
from app.services.compliance.enforce import record_violation
from app.services.video_compose import FfmpegNotFoundError, VideoComposeError, compose_video

router = APIRouter(prefix="/timeline", tags=["timeline"])
TrackName = Literal["video", "voiceover", "subtitle", "music"]


class TimelineClip(BaseModel):
    storyboard_id: int | None = None
    index: int = Field(default=0, ge=0)
    start: float = Field(default=0, ge=0)
    duration: float = Field(default=0, ge=0)
    video_url: str | None = None
    audio_url: str | None = None
    subtitle_text: str | None = None
    subtitle_url: str | None = None
    thumbnail: str | None = None


class TimelineTrack(BaseModel):
    enabled: bool = True
    clips: list[TimelineClip] = Field(default_factory=list)


class TimelineTracks(BaseModel):
    video: TimelineTrack = Field(default_factory=TimelineTrack)
    voiceover: TimelineTrack = Field(default_factory=TimelineTrack)
    subtitle: TimelineTrack = Field(default_factory=TimelineTrack)
    music: TimelineTrack = Field(default_factory=lambda: TimelineTrack(enabled=False))


class TimelineDocument(BaseModel):
    episode_id: int
    duration: float = Field(default=0, ge=0)
    tracks: TimelineTracks


class TimelineSaveRequest(BaseModel):
    tracks: TimelineTracks


class ExportRequest(BaseModel):
    username: str | None = None


def _latest_merge(db: Session, episode_id: int) -> VideoMerge | None:
    return db.scalars(
        select(VideoMerge)
        .where(
            VideoMerge.episode_id == episode_id,
            VideoMerge.provider == "timeline",
            VideoMerge.deleted_at.is_(None),
        )
        .order_by(VideoMerge.id.desc())
    ).first()


def _clip_from_storyboard(row: Storyboard, index: int, start: float) -> TimelineClip:
    return TimelineClip(
        storyboard_id=row.id,
        index=index,
        start=start,
        duration=max(float(row.duration or 0), 0),
        video_url=row.composed_video_url or row.video_url,
        audio_url=row.tts_audio_url,
        subtitle_text=row.dialogue,
        subtitle_url=row.subtitle_url,
        thumbnail=row.first_frame_image or row.composed_image,
    )


def build_timeline(db: Session, episode_id: int) -> TimelineDocument:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.deleted_at is not None:
        raise HTTPException(404, "分集不存在")

    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number, Storyboard.id)
    ).all()
    video_clips: list[TimelineClip] = []
    start = 0.0
    for index, row in enumerate(rows):
        clip = _clip_from_storyboard(row, index, start)
        video_clips.append(clip)
        start += clip.duration

    voice_clips = [clip.model_copy(deep=True) for clip in video_clips if clip.audio_url]
    subtitle_clips = [
        clip.model_copy(deep=True)
        for clip in video_clips
        if clip.subtitle_text or clip.subtitle_url
    ]
    assets = db.scalars(
        select(Asset).where(
            Asset.episode_id == episode_id,
            Asset.type == "audio",
            Asset.deleted_at.is_(None),
        )
    ).all()
    music_asset = next(
        (asset for asset in assets if (asset.category or "").lower() in {"music", "bgm", "background_music"}),
        None,
    )
    music_clips: list[TimelineClip] = []
    if music_asset and (music_asset.url or music_asset.local_path):
        music_clips.append(
            TimelineClip(
                index=0,
                start=0,
                duration=start,
                audio_url=music_asset.url or music_asset.local_path,
                thumbnail=music_asset.thumbnail_url,
            )
        )

    return TimelineDocument(
        episode_id=episode_id,
        duration=start,
        tracks=TimelineTracks(
            video=TimelineTrack(enabled=True, clips=video_clips),
            voiceover=TimelineTrack(enabled=bool(voice_clips), clips=voice_clips),
            subtitle=TimelineTrack(enabled=bool(subtitle_clips), clips=subtitle_clips),
            music=TimelineTrack(enabled=bool(music_clips), clips=music_clips),
        ),
    )


def get_timeline_document(db: Session, episode_id: int) -> TimelineDocument:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.deleted_at is not None:
        raise HTTPException(404, "分集不存在")
    merge = _latest_merge(db, episode_id)
    if merge and merge.scenes:
        try:
            return TimelineDocument.model_validate(json.loads(merge.scenes))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return build_timeline(db, episode_id)


def _normalise_timeline(db: Session, episode_id: int, tracks: TimelineTracks) -> TimelineDocument:
    board_ids = set(
        db.scalars(
            select(Storyboard.id).where(
                Storyboard.episode_id == episode_id,
                Storyboard.deleted_at.is_(None),
            )
        ).all()
    )
    video_ids = [clip.storyboard_id for clip in tracks.video.clips]
    if any(storyboard_id is None or storyboard_id not in board_ids for storyboard_id in video_ids):
        raise HTTPException(400, "视频轨包含不属于该分集的镜头")
    if len(video_ids) != len(set(video_ids)):
        raise HTTPException(400, "视频轨不能包含重复镜头")

    video = tracks.video.model_copy(deep=True)
    start = 0.0
    timing: dict[int, tuple[int, float, float]] = {}
    for index, clip in enumerate(video.clips):
        clip.index = index
        clip.start = start
        clip.duration = max(float(clip.duration), 0)
        timing[int(clip.storyboard_id)] = (index, start, clip.duration)
        start += clip.duration

    def sync(track: TimelineTrack) -> TimelineTrack:
        synced = track.model_copy(deep=True)
        unique: dict[int, TimelineClip] = {}
        for clip in synced.clips:
            if clip.storyboard_id in timing and clip.storyboard_id not in unique:
                unique[int(clip.storyboard_id)] = clip
        synced.clips = []
        for storyboard_id in video_ids:
            clip = unique.get(int(storyboard_id))
            if clip is None:
                continue
            clip.index, clip.start, clip.duration = timing[int(storyboard_id)]
            synced.clips.append(clip)
        return synced

    music = tracks.music.model_copy(deep=True)
    for index, clip in enumerate(music.clips):
        clip.index = index
        clip.start = max(clip.start, 0)
        if clip.duration <= 0:
            clip.duration = start

    return TimelineDocument(
        episode_id=episode_id,
        duration=start,
        tracks=TimelineTracks(
            video=video,
            voiceover=sync(tracks.voiceover),
            subtitle=sync(tracks.subtitle),
            music=music,
        ),
    )


def _store_timeline(db: Session, document: TimelineDocument) -> VideoMerge:
    episode = db.get(Episode, document.episode_id)
    if episode is None:
        raise HTTPException(404, "分集不存在")
    merge = _latest_merge(db, document.episode_id)
    if merge is None:
        merge = VideoMerge(
            episode_id=document.episode_id,
            drama_id=episode.drama_id,
            title=f"{episode.title} 成片",
            provider="timeline",
        )
        db.add(merge)
    merge.scenes = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
    merge.duration = round(document.duration)
    merge.status = "draft"
    merge.merged_url = None
    merge.error_msg = None
    merge.completed_at = None
    db.commit()
    db.refresh(merge)
    return merge


@router.get("/{episode_id}")
def get_timeline(episode_id: int, db: Session = Depends(get_db)) -> dict:
    return get_timeline_document(db, episode_id).model_dump(mode="json")


@router.put("/{episode_id}")
def save_timeline(episode_id: int, body: TimelineSaveRequest, db: Session = Depends(get_db)) -> dict:
    if db.get(Episode, episode_id) is None:
        raise HTTPException(404, "分集不存在")
    document = _normalise_timeline(db, episode_id, body.tracks)
    _store_timeline(db, document)
    return document.model_dump(mode="json")


@router.post("/{episode_id}/export")
def export_timeline(
    episode_id: int,
    body: ExportRequest | None = None,
    db: Session = Depends(get_db),
):
    document = get_timeline_document(db, episode_id)
    checked: set[tuple[int | None, str]] = set()
    for track_name in ("video", "voiceover", "subtitle", "music"):
        track = getattr(document.tracks, track_name)
        for clip in track.clips:
            text = (clip.subtitle_text or "").strip()
            key = (clip.storyboard_id, text)
            if not text or key in checked:
                continue
            checked.add(key)
            result = check(text)
            if result.blocked:
                enforcement = record_violation(
                    db,
                    body.username if body else None,
                    result,
                    "timeline_subtitle",
                )
                return JSONResponse(
                    status_code=451,
                    content={
                        "status": "blocked",
                        "blocked": True,
                        "storyboard_id": clip.storyboard_id,
                        "clip_index": clip.index,
                        "violation_count": enforcement["violation_count"],
                        "banned": enforcement["banned"],
                        "error": "字幕内容触发红线，已拦截并记录",
                    },
                )

    merge = _latest_merge(db, episode_id)
    if merge is None:
        merge = _store_timeline(db, document)
    merge.status = "processing"
    merge.error_msg = None
    db.commit()

    try:
        result = compose_video(document.model_dump(mode="json"), settings.data_dir / "oss")
    except FfmpegNotFoundError as exc:
        merge.status = "failed"
        merge.error_msg = str(exc)
        db.commit()
        return JSONResponse(status_code=503, content={"status": "failed", "merged_url": None, "error": str(exc)})
    except VideoComposeError as exc:
        merge.status = "failed"
        merge.error_msg = str(exc)
        db.commit()
        return JSONResponse(status_code=502, content={"status": "failed", "merged_url": None, "error": str(exc)})

    merged_url = f"/oss/{result.output_path.name}"
    merge.status = "completed"
    merge.merged_url = merged_url
    merge.duration = round(result.duration)
    merge.error_msg = None
    merge.completed_at = datetime.utcnow()
    episode = db.get(Episode, episode_id)
    episode.video_url = merged_url
    episode.duration = round(result.duration)
    db.commit()
    return {"status": "completed", "merged_url": merged_url, "error": None, "duration": result.duration}
