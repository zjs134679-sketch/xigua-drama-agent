"""本地媒体上传：图片/视频落到 data/oss，并绑定到角色/场景/道具/分镜。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.domain import Character, ImageGeneration, Prop, Scene, Storyboard

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
MAX_IMAGE_BYTES = 40 * 1024 * 1024  # 40MB
MAX_VIDEO_BYTES = 500 * 1024 * 1024  # 500MB

TARGET_TYPES = frozenset(
    {
        "character",
        "scene",
        "prop",
        "storyboard_image",
        "storyboard_video",
    }
)


class MediaUploadError(Exception):
    pass


def _ext_ok(name: str, kind: str) -> str:
    ext = Path(name or "").suffix.lower()
    if kind == "image" and ext not in ALLOWED_IMAGE_EXT:
        raise MediaUploadError(f"不支持的图片格式：{ext or '(无后缀)'}，请用 png/jpg/webp")
    if kind == "video" and ext not in ALLOWED_VIDEO_EXT:
        raise MediaUploadError(f"不支持的视频格式：{ext or '(无后缀)'}，请用 mp4/webm/mov")
    return ext or (".png" if kind == "image" else ".mp4")


def save_bytes_to_oss(
    data: bytes,
    *,
    original_name: str,
    kind: str,
    prefix: str = "upload",
) -> tuple[str, Path]:
    """写入 oss，返回 (/oss/xxx, local_path)。"""
    if not data:
        raise MediaUploadError("空文件")
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES
    if len(data) > limit:
        raise MediaUploadError(f"文件过大（上限 {limit // (1024 * 1024)}MB）")
    ext = _ext_ok(original_name, kind)
    oss = settings.data_dir / "oss"
    oss.mkdir(parents=True, exist_ok=True)
    fname = f"xigua_{prefix}_{uuid.uuid4().hex[:16]}{ext}"
    path = oss / fname
    path.write_bytes(data)
    return f"/oss/{fname}", path


def _record_upload_history(
    db: Session,
    *,
    target_type: str,
    target_id: int,
    url: str,
    path: Path,
    drama_id: int | None = None,
) -> None:
    """把本地上传记入 image_generations，历史条/刷新可立刻看到。"""
    kwargs: dict = {
        "drama_id": drama_id,
        "image_type": f"upload_{target_type}",
        "provider": "local_upload",
        "prompt": f"[本地上传] {path.name}",
        "image_url": url,
        "local_path": str(path),
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if target_type == "character":
        kwargs["character_id"] = target_id
    elif target_type == "scene":
        kwargs["scene_id"] = target_id
    elif target_type == "prop":
        kwargs["prop_id"] = target_id
    elif target_type in ("storyboard_image", "storyboard_video"):
        kwargs["storyboard_id"] = target_id
    db.add(ImageGeneration(**kwargs))


def attach_media(
    db: Session,
    *,
    target_type: str,
    target_id: int,
    data: bytes,
    original_name: str,
) -> dict:
    """上传并绑定。storyboard_video 绑视频，其它默认当图片。"""
    tt = (target_type or "").strip().lower()
    if tt not in TARGET_TYPES:
        raise MediaUploadError(f"不支持的绑定类型：{target_type}")
    kind = "video" if tt == "storyboard_video" else "image"
    url, path = save_bytes_to_oss(data, original_name=original_name, kind=kind, prefix=tt)

    if tt == "character":
        row = db.get(Character, target_id)
        if row is None or row.deleted_at is not None:
            raise LookupError("角色不存在")
        row.image_url = url
        row.local_path = str(path)
        _record_upload_history(
            db, target_type=tt, target_id=target_id, url=url, path=path, drama_id=row.drama_id
        )
        db.commit()
        return {
            "target_type": tt,
            "target_id": target_id,
            "kind": kind,
            "url": url,
            "image_url": url,
            "local_path": str(path),
        }

    if tt == "scene":
        row = db.get(Scene, target_id)
        if row is None or row.deleted_at is not None:
            raise LookupError("场景不存在")
        row.image_url = url
        row.local_path = str(path)
        row.status = "ready"
        _record_upload_history(
            db, target_type=tt, target_id=target_id, url=url, path=path, drama_id=row.drama_id
        )
        db.commit()
        return {
            "target_type": tt,
            "target_id": target_id,
            "kind": kind,
            "url": url,
            "image_url": url,
            "local_path": str(path),
        }

    if tt == "prop":
        row = db.get(Prop, target_id)
        if row is None or getattr(row, "deleted_at", None) is not None:
            raise LookupError("道具不存在")
        row.image_url = url
        if hasattr(row, "local_path"):
            row.local_path = str(path)
        _record_upload_history(
            db, target_type=tt, target_id=target_id, url=url, path=path, drama_id=row.drama_id
        )
        db.commit()
        return {
            "target_type": tt,
            "target_id": target_id,
            "kind": kind,
            "url": url,
            "image_url": url,
            "local_path": str(path),
        }

    if tt == "storyboard_image":
        row = db.get(Storyboard, target_id)
        if row is None or row.deleted_at is not None:
            raise LookupError("分镜不存在")
        row.composed_image = url
        row.first_frame_image = url
        _record_upload_history(db, target_type=tt, target_id=target_id, url=url, path=path)
        db.commit()
        return {
            "target_type": tt,
            "target_id": target_id,
            "kind": kind,
            "url": url,
            "image_url": url,
            "composed_image": url,
            "first_frame_image": url,
        }

    # storyboard_video
    row = db.get(Storyboard, target_id)
    if row is None or row.deleted_at is not None:
        raise LookupError("分镜不存在")
    row.video_url = url
    row.composed_video_url = url
    # 手动上传覆盖 AI 版本选择，避免时间线仍指向旧 VideoGeneration
    if hasattr(row, "selected_video_id"):
        row.selected_video_id = None
    # 每次上传都重抽首帧作缩略图，否则成片台仍显示旧海报，看起来像「画面不更新」
    thumb_url: str | None = None
    try:
        from app.services.video_frame import extract_video_frame

        frame = extract_video_frame(str(path), position="first", prefix="xigua_upload")
        if frame:
            thumb_url = frame
            row.first_frame_image = frame
            row.composed_image = frame
        last = extract_video_frame(str(path), position="last", prefix="xigua_last")
        if last:
            row.last_frame_image = last
    except Exception:  # noqa: BLE001
        pass
    # 视频上传也记一条，image_url 用首帧便于历史条/成片台海报
    hist_url = thumb_url or row.composed_image or row.first_frame_image or url
    _record_upload_history(db, target_type=tt, target_id=target_id, url=hist_url, path=path)
    db.commit()
    return {
        "target_type": tt,
        "target_id": target_id,
        "kind": kind,
        "url": url,
        "video_url": url,
        "image_url": thumb_url or row.composed_image or row.first_frame_image,
        "local_path": str(path),
    }
