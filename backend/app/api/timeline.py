from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.config import settings
from app.core.db import get_db
from app.models.domain import Asset, Episode, Storyboard, VideoMerge
from app.services.compliance import check
from app.services.compliance.enforce import record_violation
from app.services.video_compose import FfmpegNotFoundError, VideoComposeError, compose_video

router = APIRouter(prefix="/timeline", tags=["timeline"])
TrackName = Literal["video", "voiceover", "subtitle", "music"]

# 与本地 ComfyUI 单镜图生视频上限一致
CLIP_MAX_DURATION = 5.0


def _clamp_clip_duration(value: float | int | None) -> float:
    try:
        raw = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if raw <= 0:
        return 0.0
    return min(raw, CLIP_MAX_DURATION)


def _oss_exists(name: str) -> bool:
    path = (settings.data_dir / "oss" / Path(name).name).resolve()
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def normalize_media_url(url: str | None) -> str | None:
    """把 ComfyUI /view 直链转成可播放的 /oss/ 路径。

    - `/oss/xxx` 且文件存在 → 保留
    - `/oss/xxx` 文件缺失 → None（避免前端 404 黑屏）
    - ComfyUI view 链 → 若本地 oss 有同名文件则改写
    - 其它相对路径（测试/外部）原样返回
    """
    if not url or not str(url).strip():
        return None
    text = str(url).strip()
    if text.startswith("/oss/"):
        name = Path(text).name
        return f"/oss/{name}" if _oss_exists(name) else None
    # ComfyUI: http://127.0.0.1:8188/view?filename=xigua_i2v_00005_.mp4&...
    if "filename=" in text and ("/view" in text or "8188" in text):
        try:
            qs = parse_qs(urlparse(text).query)
            name = (qs.get("filename") or [None])[0]
            if name and _oss_exists(name):
                return f"/oss/{Path(name).name}"
            # Comfy 直链在浏览器常因跨域/未开机失败；无本地副本则清空
            return None
        except Exception:  # noqa: BLE001
            return None
    # 绝对本地路径 → 若落在 oss 目录则改写
    try:
        path = Path(text)
        if path.is_file():
            oss_root = (settings.data_dir / "oss").resolve()
            resolved = path.resolve()
            if oss_root in resolved.parents or resolved.parent == oss_root:
                return f"/oss/{resolved.name}"
    except OSError:
        pass
    if text.startswith("http://") or text.startswith("https://"):
        return text
    # 测试占位或相对路径：原样保留
    return text


def _refresh_clip_from_storyboard(clip: TimelineClip, sb: Storyboard) -> TimelineClip:
    """用分镜表最新素材覆盖缓存时间线里的媒体字段（解决「已出视频但成片台空白」）。"""
    video = normalize_media_url(sb.composed_video_url or sb.video_url)
    thumb = normalize_media_url(sb.first_frame_image or sb.composed_image or sb.last_frame_image)
    # 不再使用 tts_audio_url；模型语音在 video 文件内
    clip.video_url = video
    clip.thumbnail = thumb
    clip.audio_url = None
    if sb.dialogue is not None and (clip.subtitle_text is None or clip.subtitle_text == ""):
        clip.subtitle_text = sb.dialogue
    if sb.segment_total and sb.segment_total >= 2:
        clip.segment_key = sb.segment_key
        clip.segment_title = sb.segment_title
        clip.segment_part = sb.segment_part
        clip.segment_total = sb.segment_total
    else:
        clip.segment_key = None
        clip.segment_title = None
        clip.segment_part = None
        clip.segment_total = None
    clip.trim_in = getattr(sb, "trim_in", None)
    clip.trim_out = getattr(sb, "trim_out", None)
    clip.selected_video_id = getattr(sb, "selected_video_id", None)
    return clip


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
    segment_key: str | None = None
    segment_title: str | None = None
    segment_part: int | None = None
    segment_total: int | None = None
    trim_in: float | None = None
    trim_out: float | None = None
    selected_video_id: int | None = None


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
    # 异步入队（长成片推荐）；默认 false 保持同步返回
    async_mode: bool = False


class TTSRequest(BaseModel):
    username: str | None = None


def _latest_merge(db: Session, episode_id: int) -> VideoMerge | None:
    """取最新时间线草稿（兼容 timeline / easy_pipeline 等 provider）。"""
    return db.scalars(
        select(VideoMerge)
        .where(
            VideoMerge.episode_id == episode_id,
            VideoMerge.deleted_at.is_(None),
        )
        .order_by(VideoMerge.id.desc())
    ).first()


def _clip_from_storyboard(row: Storyboard, index: int, start: float) -> TimelineClip:
    clip = TimelineClip(
        storyboard_id=row.id,
        index=index,
        start=start,
        duration=_clamp_clip_duration(row.duration),
        video_url=None,
        audio_url=None,
        subtitle_text=row.dialogue,
        subtitle_url=row.subtitle_url,
        thumbnail=None,
    )
    return _refresh_clip_from_storyboard(clip, row)


def build_timeline(db: Session, episode_id: int) -> TimelineDocument:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.deleted_at is not None:
        raise HTTPException(404, "分集不存在")

    from app.services.storyboard_segments import refresh_episode_segments

    refresh_episode_segments(db, episode_id, commit=True)

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


def _reconcile_timeline_with_storyboards(db: Session, episode_id: int, document: TimelineDocument) -> TimelineDocument:
    """缓存时间线与分镜表对齐：补上拆镜新增的镜头、去掉已删镜头，按镜号排序。

    否则会出现「分镜 18 条各 5s，成片台还停在旧的 10 条/甚至看起来像只有 5s」。
    """
    boards = list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number, Storyboard.id)
        ).all()
    )
    board_ids = {row.id for row in boards}
    by_clip: dict[int, TimelineClip] = {}
    for clip in document.tracks.video.clips:
        if clip.storyboard_id is not None and int(clip.storyboard_id) in board_ids:
            by_clip[int(clip.storyboard_id)] = clip.model_copy(deep=True)

    video_clips: list[TimelineClip] = []
    start = 0.0
    for index, row in enumerate(boards):
        existing = by_clip.get(row.id)
        if existing is not None:
            clip = existing
            clip.index = index
            clip.start = start
            # 时长以分镜为准（钳到 5s），保留 trim / selected_video
            if row.duration and row.duration > 0:
                clip.duration = _clamp_clip_duration(float(row.duration))
            else:
                clip.duration = _clamp_clip_duration(clip.duration)
            _refresh_clip_from_storyboard(clip, row)
        else:
            clip = _clip_from_storyboard(row, index, start)
        video_clips.append(clip)
        start += clip.duration

    # 旁白/字幕：按视频轨重建，媒体从 clip 同步
    voice_clips = [clip.model_copy(deep=True) for clip in video_clips if clip.audio_url]
    subtitle_clips = [
        clip.model_copy(deep=True) for clip in video_clips if clip.subtitle_text or clip.subtitle_url
    ]
    music = document.tracks.music.model_copy(deep=True)
    for clip in music.clips:
        # 音乐轨总长跟成片对齐
        if music.clips and clip is music.clips[0]:
            clip.duration = start

    return TimelineDocument(
        episode_id=episode_id,
        duration=start,
        tracks=TimelineTracks(
            video=TimelineTrack(enabled=True, clips=video_clips),
            voiceover=TimelineTrack(enabled=bool(voice_clips), clips=voice_clips),
            subtitle=TimelineTrack(enabled=bool(subtitle_clips), clips=subtitle_clips),
            music=music,
        ),
    )


def get_timeline_document(db: Session, episode_id: int) -> TimelineDocument:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.deleted_at is not None:
        raise HTTPException(404, "分集不存在")
    merge = _latest_merge(db, episode_id)
    if merge and merge.scenes:
        try:
            raw = TimelineDocument.model_validate(json.loads(merge.scenes))
            document = _normalise_timeline(db, episode_id, raw.tracks)
            # 始终用分镜表最新 video/缩略图/配音覆盖缓存（出片后不必重存时间线）
            document = _hydrate_timeline_media(db, document)
            # 拆镜/删镜后：与分镜表全量对齐（补新镜、去旧镜）
            before_sig = _timeline_structure_signature(document)
            document = _reconcile_timeline_with_storyboards(db, episode_id, document)
            after_sig = _timeline_structure_signature(document)
            # 时长钳制或结构/媒体有更新时写回缓存
            needs_clamp = any(
                float(c.duration or 0) > CLIP_MAX_DURATION for c in raw.tracks.video.clips
            )
            media_changed = _timeline_media_signature(raw) != _timeline_media_signature(document)
            structure_changed = before_sig != after_sig or len(raw.tracks.video.clips) != len(
                document.tracks.video.clips
            )
            if needs_clamp or media_changed or structure_changed:
                for clip in document.tracks.video.clips:
                    if clip.storyboard_id is None:
                        continue
                    sb = db.get(Storyboard, clip.storyboard_id)
                    if sb is not None and (not sb.duration or sb.duration > CLIP_MAX_DURATION):
                        sb.duration = int(round(clip.duration)) or 5
                _store_timeline(db, document)
            return document
        except (json.JSONDecodeError, ValueError, TypeError, HTTPException):
            pass
    return build_timeline(db, episode_id)


def _timeline_structure_signature(doc: TimelineDocument) -> tuple:
    return tuple(
        (c.storyboard_id, round(float(c.duration or 0), 3), c.segment_key, c.segment_part)
        for c in doc.tracks.video.clips
    )


def _timeline_media_signature(doc: TimelineDocument) -> tuple:
    return tuple(
        (
            c.storyboard_id,
            c.video_url,
            c.thumbnail,
            c.audio_url,
            round(float(c.duration or 0), 3),
        )
        for c in doc.tracks.video.clips
    )


def _hydrate_timeline_media(db: Session, document: TimelineDocument) -> TimelineDocument:
    """从 storyboards 回填最新视频/图/配音，避免缓存 scenes 缺 video_url。"""
    ids = [c.storyboard_id for c in document.tracks.video.clips if c.storyboard_id is not None]
    if not ids:
        return document
    rows = {
        row.id: row
        for row in db.scalars(select(Storyboard).where(Storyboard.id.in_(ids))).all()
    }
    video = document.tracks.video.model_copy(deep=True)
    for clip in video.clips:
        sb = rows.get(int(clip.storyboard_id)) if clip.storyboard_id is not None else None
        if sb is not None:
            _refresh_clip_from_storyboard(clip, sb)

    def sync_media(track: TimelineTrack, field: str) -> TimelineTrack:
        synced = track.model_copy(deep=True)
        by_id = {int(c.storyboard_id): c for c in synced.clips if c.storyboard_id is not None}
        for clip in video.clips:
            if clip.storyboard_id is None:
                continue
            other = by_id.get(int(clip.storyboard_id))
            if other is None:
                continue
            if field == "audio":
                other.audio_url = clip.audio_url
            elif field == "subtitle":
                if clip.subtitle_text:
                    other.subtitle_text = clip.subtitle_text
            other.duration = clip.duration
            other.start = clip.start
            other.index = clip.index
        return synced

    return TimelineDocument(
        episode_id=document.episode_id,
        duration=document.duration,
        tracks=TimelineTracks(
            video=video,
            voiceover=sync_media(document.tracks.voiceover, "audio"),
            subtitle=sync_media(document.tracks.subtitle, "subtitle"),
            music=document.tracks.music,
        ),
    )


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
        clip.duration = _clamp_clip_duration(clip.duration)
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


def _refresh_generated_audio_tracks(db: Session, episode_id: int) -> None:
    """Keep saved manual video/music ordering while pulling current DB voice/subtitle clips."""
    stored = _latest_merge(db, episode_id)
    if not stored or not stored.scenes:
        return
    document = get_timeline_document(db, episode_id)
    fresh = build_timeline(db, episode_id)
    tracks = TimelineTracks(
        video=document.tracks.video,
        voiceover=fresh.tracks.voiceover,
        subtitle=fresh.tracks.subtitle,
        music=document.tracks.music,
    )
    try:
        refreshed = _normalise_timeline(db, episode_id, tracks)
    except HTTPException:
        refreshed = fresh
    _store_timeline(db, refreshed)


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


def run_timeline_export(
    db: Session,
    episode_id: int,
    *,
    username: str | None = None,
) -> dict:
    """同步导出成片（应用 trim_in/out）。供 API 与任务队列共用。"""
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
                    username,
                    result,
                    "timeline_subtitle",
                )
                return {
                    "status": "blocked",
                    "blocked": True,
                    "storyboard_id": clip.storyboard_id,
                    "clip_index": clip.index,
                    "violation_count": enforcement["violation_count"],
                    "banned": enforcement["banned"],
                    "error": "字幕内容触发红线，已拦截并记录",
                    "http_status": 451,
                }

    merge = _latest_merge(db, episode_id)
    if merge is None:
        merge = _store_timeline(db, document)
    merge.status = "processing"
    merge.error_msg = None
    db.commit()

    oss_dir = settings.data_dir / "oss"
    timeline_payload = document.model_dump(mode="json")
    # 从已存 scenes 读转场/配乐偏好（一键出片写入；手工导出默认真淡入）
    if merge and merge.scenes:
        try:
            raw_scenes = json.loads(merge.scenes)
            if isinstance(raw_scenes, dict):
                if raw_scenes.get("transition"):
                    timeline_payload["transition"] = raw_scenes["transition"]
                if raw_scenes.get("transition_duration") is not None:
                    timeline_payload["transition_duration"] = raw_scenes["transition_duration"]
                # 若当前文档音乐轨空，但缓存里有 music，则带上
                music_raw = (raw_scenes.get("tracks") or {}).get("music") or {}
                if music_raw.get("clips") and not timeline_payload["tracks"]["music"]["clips"]:
                    timeline_payload["tracks"]["music"] = music_raw
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    if "transition" not in timeline_payload:
        timeline_payload["transition"] = "fade"
        timeline_payload["transition_duration"] = 0.35

    try:
        result = compose_video(
            timeline_payload,
            oss_dir,
            oss_dir=oss_dir,
        )
    except FfmpegNotFoundError as exc:
        merge.status = "failed"
        merge.error_msg = str(exc)
        db.commit()
        return {"status": "failed", "merged_url": None, "error": str(exc), "http_status": 503}
    except VideoComposeError as exc:
        merge.status = "failed"
        merge.error_msg = str(exc)
        db.commit()
        return {"status": "failed", "merged_url": None, "error": str(exc), "http_status": 502}

    merged_url = f"/oss/{result.output_path.name}"
    merge.status = "completed"
    merge.merged_url = merged_url
    merge.duration = round(result.duration)
    merge.error_msg = None
    merge.completed_at = datetime.utcnow()
    episode = db.get(Episode, episode_id)
    if episode is not None:
        episode.video_url = merged_url
        episode.duration = round(result.duration)
    db.commit()
    return {
        "status": "completed",
        "merged_url": merged_url,
        "error": None,
        "duration": result.duration,
        "http_status": 200,
    }


@router.post("/{episode_id}/export")
def export_timeline(
    episode_id: int,
    body: ExportRequest | None = None,
    db: Session = Depends(get_db),
):
    username = body.username if body else None
    ensure_active_user(db, username)
    if body and body.async_mode:
        from app.services.jobs import enqueue_job, job_view

        ep = db.get(Episode, episode_id)
        job = enqueue_job(
            db,
            job_type="export_timeline",
            payload={"episode_id": episode_id, "username": username},
            drama_id=ep.drama_id if ep else None,
            episode_id=episode_id,
            username=username,
            message="导出成片",
        )
        return {"async": True, "status": "pending", "job": job_view(job)}

    payload = run_timeline_export(db, episode_id, username=username)
    http_status = int(payload.pop("http_status", 200))
    if http_status != 200:
        return JSONResponse(status_code=http_status, content=payload)
    return payload


@router.post("/storyboards/{storyboard_id}/tts")
def generate_storyboard_tts_removed(storyboard_id: int):
    """已移除独立 TTS。语音由视频模型在「定稿出片」时直接生成。"""
    raise HTTPException(
        410,
        "已取消独立配音。请直接「定稿出片」：台词会写入视频提示词，由视频模型生成语音。",
    )


@router.post("/{episode_id}/tts")
def generate_timeline_tts_removed(episode_id: int):
    raise HTTPException(
        410,
        "已取消独立配音。请对分镜批量「定稿出片」，模型会直接生成带语音的镜头。",
    )
