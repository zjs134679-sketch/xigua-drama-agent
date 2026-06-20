from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VideoComposeError(RuntimeError):
    pass


class FfmpegNotFoundError(VideoComposeError):
    pass


@dataclass
class ComposeResult:
    output_path: Path
    duration: float
    commands: list[list[str]]


Runner = Callable[..., Any]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default


def _track(timeline: dict[str, Any], name: str) -> tuple[bool, list[dict[str, Any]]]:
    value = timeline.get("tracks", {}).get(name, {})
    return bool(value.get("enabled", True)), list(value.get("clips") or [])


def _source(clip: dict[str, Any], field: str) -> str | None:
    value = clip.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _run(command: list[str], runner: Runner) -> None:
    try:
        runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FfmpegNotFoundError("未找到 ffmpeg，请先安装 ffmpeg 并加入 PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "ffmpeg 执行失败").strip()
        raise VideoComposeError(detail[-1000:]) from exc


def _timestamp(seconds: float) -> str:
    millis = max(round(seconds * 1000), 0)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(path: Path, clips: list[dict[str, Any]]) -> bool:
    blocks: list[str] = []
    for clip in sorted(clips, key=lambda item: (_number(item.get("start")), int(item.get("index") or 0))):
        text = str(clip.get("subtitle_text") or "").strip()
        if not text:
            continue
        start = _number(clip.get("start"))
        end = start + max(_number(clip.get("duration")), 0.1)
        clean_text = text.replace("\r\n", "\n").replace("\r", "\n")
        blocks.append(f"{len(blocks) + 1}\n{_timestamp(start)} --> {_timestamp(end)}\n{clean_text}\n")
    if not blocks:
        return False
    path.write_text("\n".join(blocks), encoding="utf-8")
    return True


def compose_video(
    timeline: dict[str, Any],
    output_dir: Path,
    *,
    runner: Runner | None = None,
    ffmpeg_path: str | None = None,
) -> ComposeResult:
    runner = runner or subprocess.run
    executable = ffmpeg_path or shutil.which("ffmpeg")
    if not executable:
        raise FfmpegNotFoundError("未找到 ffmpeg，请先安装 ffmpeg 并加入 PATH")

    video_enabled, video_clips = _track(timeline, "video")
    videos = [(clip, _source(clip, "video_url")) for clip in video_clips]
    videos = [(clip, source) for clip, source in videos if source]
    if not video_enabled or not videos:
        raise VideoComposeError("时间线没有可导出的视频片段")

    total_duration = _number(timeline.get("duration"))
    if total_duration <= 0:
        total_duration = sum(_number(clip.get("duration")) for clip, _ in videos)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"episode-{timeline.get('episode_id', 'unknown')}-{uuid.uuid4().hex}.mp4"
    commands: list[list[str]] = []

    with tempfile.TemporaryDirectory(prefix="timeline-", dir=output_dir) as temporary:
        work_dir = Path(temporary)
        concatenated = work_dir / "video.mp4"
        concat_command = [executable, "-y"]
        for clip, source in videos:
            duration = _number(clip.get("duration"))
            if duration > 0:
                concat_command.extend(["-t", f"{duration:.3f}"])
            concat_command.extend(["-i", source])

        filters: list[str] = []
        labels: list[str] = []
        for index in range(len(videos)):
            label = f"v{index}"
            filters.append(
                f"[{index}:v:0]setpts=PTS-STARTPTS,"
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps=25,format=yuv420p[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vout]")
        concat_command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(concatenated),
            ]
        )
        commands.append(concat_command)
        _run(concat_command, runner)

        voice_enabled, voice_clips = _track(timeline, "voiceover")
        music_enabled, music_clips = _track(timeline, "music")
        voices = [(clip, _source(clip, "audio_url")) for clip in voice_clips] if voice_enabled else []
        music = [(clip, _source(clip, "audio_url")) for clip in music_clips] if music_enabled else []
        voices = [(clip, source) for clip, source in voices if source]
        music = [(clip, source) for clip, source in music if source]
        current = concatenated

        if voices or music:
            mixed = work_dir / "audio.mp4"
            audio_command = [executable, "-y", "-i", str(concatenated)]
            for _, source in voices:
                audio_command.extend(["-i", source])
            for _, source in music:
                audio_command.extend(["-stream_loop", "-1", "-i", source])

            audio_filters: list[str] = []
            audio_labels: list[str] = []
            input_index = 1
            for clip, _ in voices:
                label = f"a{input_index}"
                start_ms = round(_number(clip.get("start")) * 1000)
                duration = _number(clip.get("duration")) or total_duration
                audio_filters.append(
                    f"[{input_index}:a:0]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
                    f"adelay={start_ms}:all=1[{label}]"
                )
                audio_labels.append(f"[{label}]")
                input_index += 1
            for _, _source_value in music:
                label = f"a{input_index}"
                audio_filters.append(
                    f"[{input_index}:a:0]atrim=0:{total_duration:.3f},"
                    f"asetpts=PTS-STARTPTS,volume=0.18[{label}]"
                )
                audio_labels.append(f"[{label}]")
                input_index += 1
            audio_filters.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
                f"duration=longest:dropout_transition=0,atrim=0:{total_duration:.3f}[aout]"
            )
            audio_command.extend(
                [
                    "-filter_complex",
                    ";".join(audio_filters),
                    "-map",
                    "0:v:0",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(mixed),
                ]
            )
            commands.append(audio_command)
            _run(audio_command, runner)
            current = mixed

        subtitle_enabled, subtitle_clips = _track(timeline, "subtitle")
        subtitle_path = work_dir / "subtitles.srt"
        has_subtitles = subtitle_enabled and _write_srt(subtitle_path, subtitle_clips)
        if has_subtitles:
            subtitle_command = [
                executable,
                "-y",
                "-i",
                str(current),
                "-i",
                str(subtitle_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-map",
                "1:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=chi",
                str(output_path),
            ]
            commands.append(subtitle_command)
            _run(subtitle_command, runner)
        else:
            shutil.copyfile(current, output_path)

    return ComposeResult(output_path=output_path, duration=total_duration, commands=commands)
