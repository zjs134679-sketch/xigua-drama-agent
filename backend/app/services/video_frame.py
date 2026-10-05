"""从成片视频抽取首/尾帧，供下一镜图生视频衔接；媒体时长探测。"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    return shutil.which("ffprobe") or None


def resolve_media_path(url_or_path: str | None) -> Path | None:
    if not url_or_path:
        return None
    text = str(url_or_path).strip()
    if not text:
        return None
    if text.startswith("/oss/"):
        candidate = settings.data_dir / "oss" / Path(text).name
        return candidate if candidate.is_file() else None
    path = Path(text)
    if path.is_file():
        return path
    # 纯文件名
    candidate = settings.data_dir / "oss" / Path(text).name
    return candidate if candidate.is_file() else None


def extract_video_frame(
    video_url_or_path: str,
    *,
    position: str = "last",
    prefix: str = "xigua_frame",
) -> str | None:
    """抽取视频首帧或尾帧，写入 data/oss，返回 /oss/xxx.png。

    position: "last" | "first"
    """
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg 未安装，无法抽取视频帧做运镜衔接")
        return None
    source = resolve_media_path(video_url_or_path)
    if source is None:
        logger.warning("视频文件不存在，无法抽帧: %s", video_url_or_path)
        return None

    oss_dir = settings.data_dir / "oss"
    oss_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"{prefix}_{uuid.uuid4().hex[:12]}.png"
    out_path = oss_dir / out_name

    if position == "first":
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-frames:v", "1",
            "-q:v", "2",
            str(out_path),
        ]
    else:
        # 接近结尾取一帧；短片若 -sseof 失败再回退 seek 百分比
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-sseof", "-0.12",
            "-i", str(source),
            "-frames:v", "1",
            "-q:v", "2",
            str(out_path),
        ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("抽帧失败(%s): %s", position, exc)
        if position == "last":
            # 回退：取第一帧也比没有好
            return extract_video_frame(video_url_or_path, position="first", prefix=prefix)
        return None
    if not out_path.is_file() or out_path.stat().st_size < 32:
        return None
    return f"/oss/{out_name}"


def probe_media_duration_seconds(url_or_path: str | None) -> float | None:
    """返回音视频时长（秒）；失败返回 None。"""
    if not url_or_path:
        return None
    source = resolve_media_path(url_or_path)
    if source is None:
        return None
    probe = _ffprobe()
    if probe:
        try:
            proc = subprocess.run(
                [
                    probe, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(source),
                ],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return max(float(proc.stdout.strip()), 0.0)
        except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("ffprobe 时长失败: %s", exc)
    # 无 ffprobe 时用 ffmpeg -i 解析
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", str(source)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        # Duration: 00:00:05.12
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if not m:
            return None
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
