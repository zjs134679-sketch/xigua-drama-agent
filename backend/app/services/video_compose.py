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


def resolve_media_path(url: str | None, oss_dir: Path | None = None) -> str | None:
    """把 /oss/xxx 或纯文件名解析为本地路径；其它原样返回（供 ffmpeg 读）。"""
    if not url or not str(url).strip():
        return None
    text = str(url).strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    name = Path(text).name
    if text.startswith("/oss/") or (not Path(text).is_absolute() and name):
        if oss_dir is not None:
            candidate = (oss_dir / name).resolve()
            if candidate.is_file():
                return str(candidate)
        # 允许测试用相对路径/占位名
        if Path(text).is_file():
            return str(Path(text).resolve())
        return text if not text.startswith("/oss/") else str((oss_dir or Path(".")) / name)
    if Path(text).is_file():
        return str(Path(text).resolve())
    return text


def clip_trim_window(clip: dict[str, Any]) -> tuple[float, float]:
    """返回 (trim_in, duration) 用于裁切源素材。

    - 有 trim_in/trim_out：从 trim_in 切到 trim_out
    - 仅 trim_in：从 trim_in 起取 duration
    - 仅 trim_out：从 0 切到 trim_out
    - 都无：从 0 起取 duration
    """
    duration = _number(clip.get("duration"))
    raw_in = clip.get("trim_in")
    raw_out = clip.get("trim_out")
    trim_in = _number(raw_in) if raw_in is not None and raw_in != "" else 0.0
    if raw_out is not None and raw_out != "":
        trim_out = _number(raw_out)
        if trim_out > trim_in:
            return trim_in, max(trim_out - trim_in, 0.05)
    if duration > 0:
        return trim_in, duration
    return trim_in, 0.0


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


def _map_transition(name: str | None) -> str | None:
    """自研转场映射（仅用 ffmpeg 内置 xfade，无第三方素材）。"""
    key = (name or "none").strip().lower()
    if key in ("", "none", "cut", "hard"):
        return None
    allowed = {
        "fade": "fade",
        "fadeblack": "fadeblack",
        "fadewhite": "fadewhite",
        "dissolve": "dissolve",
        "smoothleft": "smoothleft",
        "smoothright": "smoothright",
    }
    return allowed.get(key, "fade")


def compose_video(
    timeline: dict[str, Any],
    output_dir: Path,
    *,
    runner: Runner | None = None,
    ffmpeg_path: str | None = None,
    oss_dir: Path | None = None,
) -> ComposeResult:
    runner = runner or subprocess.run
    executable = ffmpeg_path or shutil.which("ffmpeg")
    if not executable:
        raise FfmpegNotFoundError("未找到 ffmpeg，请先安装 ffmpeg 并加入 PATH")

    media_root = oss_dir or output_dir
    video_enabled, video_clips = _track(timeline, "video")
    videos = [(clip, resolve_media_path(_source(clip, "video_url"), media_root)) for clip in video_clips]
    videos = [(clip, source) for clip, source in videos if source]
    if not video_enabled or not videos:
        raise VideoComposeError("时间线没有可导出的视频片段")

    xfade = _map_transition(timeline.get("transition"))
    xfade_dur = _number(timeline.get("transition_duration"), 0.35)
    if xfade_dur <= 0:
        xfade_dur = 0.35
    # 单镜过短时关闭转场，避免 xfade 失败
    if xfade and len(videos) >= 2:
        min_take = min(
            max(clip_trim_window(c)[1], _number(c.get("duration")), 0.1) for c, _ in videos
        )
        if min_take <= xfade_dur + 0.15:
            xfade = None

    # 按 trim 重算各镜有效时长与累计 start（字幕/配音对齐；有转场时 start 会重叠缩短）
    cursor = 0.0
    normalized: list[tuple[dict[str, Any], str, float, float]] = []
    for index, (clip, source) in enumerate(videos):
        trim_in, take = clip_trim_window(clip)
        if take <= 0:
            take = max(_number(clip.get("duration")), 0.1)
        work = dict(clip)
        work["start"] = cursor
        work["duration"] = take
        work["_trim_in"] = trim_in
        work["_take"] = take
        normalized.append((work, source, trim_in, take))
        if xfade and index < len(videos) - 1:
            cursor += max(take - xfade_dur, 0.05)
        else:
            cursor += take
    total_duration = cursor if cursor > 0 else _number(timeline.get("duration"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"episode-{timeline.get('episode_id', 'unknown')}-{uuid.uuid4().hex}.mp4"
    commands: list[list[str]] = []

    with tempfile.TemporaryDirectory(prefix="timeline-", dir=output_dir) as temporary:
        work_dir = Path(temporary)
        concatenated = work_dir / "video.mp4"
        concat_command = [executable, "-y"]
        # 每段：-ss 入点 -t 时长 -i 源（参数列表，禁 shell）
        for _clip, source, trim_in, take in normalized:
            if trim_in > 0:
                concat_command.extend(["-ss", f"{trim_in:.3f}"])
            if take > 0:
                concat_command.extend(["-t", f"{take:.3f}"])
            concat_command.extend(["-i", source])

        # 视频 + 模型原生音轨拼接（已取消独立 TTS）
        n = len(normalized)
        filters: list[str] = []
        for index in range(n):
            take = max(normalized[index][3], 0.1)
            filters.append(
                f"[{index}:v:0]setpts=PTS-STARTPTS,"
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps=25,format=yuv420p[v{index}]"
            )
            filters.append(
                f"[{index}:a:0]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"atrim=0:{take:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={take:.3f}[a{index}]"
            )
        if xfade and n >= 2:
            prev = "v0"
            for index in range(1, n):
                prev_take = normalized[index - 1][3]
                if index == 1:
                    chain_offset = max(prev_take - xfade_dur, 0.05)
                else:
                    chain_offset = max(float(normalized[index][0]["start"]), 0.05)
                out_lab = "vout" if index == n - 1 else f"x{index}"
                filters.append(
                    f"[{prev}][v{index}]xfade=transition={xfade}:duration={xfade_dur:.3f}"
                    f":offset={chain_offset:.3f}[{out_lab}]"
                )
                prev = out_lab
            # 音频 concat（硬切，与视频转场时长近似）
            alabels = "".join(f"[a{i}]" for i in range(n))
            filters.append(f"{alabels}concat=n={n}:v=0:a=1[aout]")
            map_audio = "[aout]"
        else:
            labels = "".join(f"[v{i}]" for i in range(n))
            alabels = "".join(f"[a{i}]" for i in range(n))
            filters.append(f"{labels}concat=n={n}:v=1:a=0[vout]")
            filters.append(f"{alabels}concat=n={n}:v=0:a=1[aout]")
            map_audio = "[aout]"
        # 尝试带原生音轨拼接；若源无音轨失败，回退静音视频
        concat_with_audio = concat_command + [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            map_audio,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(concatenated),
        ]
        commands.append(concat_with_audio)
        try:
            _run(concat_with_audio, runner)
        except VideoComposeError:
            # 回退：仅视频（部分镜头无音轨时）
            filters_v: list[str] = []
            for index in range(n):
                filters_v.append(
                    f"[{index}:v:0]setpts=PTS-STARTPTS,"
                    "scale=1280:720:force_original_aspect_ratio=decrease,"
                    "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"setsar=1,fps=25,format=yuv420p[v{index}]"
                )
            if xfade and n >= 2:
                prev = "v0"
                for index in range(1, n):
                    prev_take = normalized[index - 1][3]
                    chain_offset = (
                        max(prev_take - xfade_dur, 0.05)
                        if index == 1
                        else max(float(normalized[index][0]["start"]), 0.05)
                    )
                    out_lab = "vout" if index == n - 1 else f"x{index}"
                    filters_v.append(
                        f"[{prev}][v{index}]xfade=transition={xfade}:duration={xfade_dur:.3f}"
                        f":offset={chain_offset:.3f}[{out_lab}]"
                    )
                    prev = out_lab
            else:
                labels = "".join(f"[v{i}]" for i in range(n))
                filters_v.append(f"{labels}concat=n={n}:v=1:a=0[vout]")
            silent_cmd = concat_command + [
                "-filter_complex",
                ";".join(filters_v),
                "-map",
                "[vout]",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(concatenated),
            ]
            commands.append(silent_cmd)
            _run(silent_cmd, runner)

        music_enabled, music_clips = _track(timeline, "music")
        start_by_sb = {
            c.get("storyboard_id"): (c.get("start"), c.get("duration"))
            for c, _, _, _ in normalized
            if c.get("storyboard_id") is not None
        }
        # 已取消独立 TTS：voiceover 轨忽略；仅混 BGM（若有）
        music = [
            (clip, resolve_media_path(_source(clip, "audio_url"), media_root))
            for clip in (music_clips if music_enabled else [])
        ]
        music = [(clip, source) for clip, source in music if source]
        current = concatenated

        if music:
            mixed = work_dir / "audio.mp4"
            audio_command = [executable, "-y", "-i", str(concatenated)]
            for _, source in music:
                audio_command.extend(["-stream_loop", "-1", "-i", source])
            audio_filters: list[str] = []
            audio_labels = ["[va]"]
            audio_filters = [
                f"[0:a]volume=1.0,atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS[va]"
            ]
            input_index = 1
            for _, _source_value in music:
                label = f"m{input_index}"
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
            try:
                _run(audio_command, runner)
                current = mixed
            except VideoComposeError:
                # 无原生音轨时只混 BGM 到静音画面
                bgm_only = [executable, "-y", "-i", str(concatenated)]
                for _, source in music:
                    bgm_only.extend(["-stream_loop", "-1", "-i", source])
                bgm_filters = []
                labels = []
                for i in range(len(music)):
                    lab = f"b{i}"
                    bgm_filters.append(
                        f"[{i+1}:a:0]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,volume=0.18[{lab}]"
                    )
                    labels.append(f"[{lab}]")
                bgm_filters.append(
                    f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest,"
                    f"atrim=0:{total_duration:.3f}[aout]"
                )
                bgm_only.extend(
                    [
                        "-filter_complex",
                        ";".join(bgm_filters),
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
                commands.append(bgm_only)
                _run(bgm_only, runner)
                current = mixed

        subtitle_enabled, subtitle_clips = _track(timeline, "subtitle")
        aligned_subs: list[dict[str, Any]] = []
        if subtitle_enabled:
            for clip in subtitle_clips:
                work = dict(clip)
                sb_id = work.get("storyboard_id")
                if sb_id in start_by_sb:
                    st, du = start_by_sb[sb_id]
                    work["start"] = st
                    work["duration"] = du
                aligned_subs.append(work)
        subtitle_path = work_dir / "subtitles.srt"
        has_subtitles = bool(aligned_subs) and _write_srt(subtitle_path, aligned_subs)
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
