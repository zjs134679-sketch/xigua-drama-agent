"""程序化背景配乐（完全自研、无第三方素材版权）。

用 ffmpeg 合成柔和环境音垫，仅作短剧成片衬底，不引用任何商业曲库。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.logging import logger


class BgmSynthError(RuntimeError):
    pass


# 自研「情绪垫」预设：仅参数，无采样文件
MOOD_PRESETS: dict[str, dict[str, Any]] = {
    "warm": {
        "label": "温暖日常",
        "freqs": (220.0, 277.18, 329.63),  # A3 C#4 E4 小三和声感
        "noise": 0.012,
        "lfo_hz": 0.08,
    },
    "tense": {
        "label": "紧张悬疑",
        "freqs": (110.0, 164.81, 233.08),
        "noise": 0.02,
        "lfo_hz": 0.15,
    },
    "epic": {
        "label": "史诗铺陈",
        "freqs": (98.0, 146.83, 196.0, 293.66),
        "noise": 0.01,
        "lfo_hz": 0.06,
    },
    "soft": {
        "label": "柔和旁白",
        "freqs": (196.0, 246.94, 293.66),
        "noise": 0.008,
        "lfo_hz": 0.05,
    },
}


def list_moods() -> list[dict[str, str]]:
    return [{"id": k, "label": str(v["label"])} for k, v in MOOD_PRESETS.items()]


def resolve_mood(mood: str | None) -> str:
    key = (mood or "warm").strip().lower()
    return key if key in MOOD_PRESETS else "warm"


def _cache_path(oss_dir: Path, mood: str, duration: float) -> Path:
    # 按整数秒缓存，避免每次导出都重算
    sec = max(8, int(round(duration)))
    digest = hashlib.sha1(f"xg-bgm-v1|{mood}|{sec}".encode()).hexdigest()[:12]
    return oss_dir / f"xg_bgm_{mood}_{sec}s_{digest}.mp3"


def ensure_bgm_file(
    oss_dir: Path,
    *,
    duration: float,
    mood: str | None = "warm",
    ffmpeg_path: str | None = None,
) -> Path:
    """生成或复用缓存的程序化 BGM 文件，返回本地路径。"""
    mood_key = resolve_mood(mood)
    preset = MOOD_PRESETS[mood_key]
    oss_dir.mkdir(parents=True, exist_ok=True)
    out = _cache_path(oss_dir, mood_key, duration)
    if out.is_file() and out.stat().st_size > 800:
        return out

    executable = ffmpeg_path or shutil.which("ffmpeg")
    if not executable:
        raise BgmSynthError("未找到 ffmpeg，无法生成程序化配乐")

    sec = max(8.0, float(duration) + 1.5)
    freqs: tuple[float, ...] = tuple(preset["freqs"])  # type: ignore[assignment]
    noise = float(preset["noise"])
    lfo = float(preset["lfo_hz"])

    # 多层正弦 + 极轻粉噪 + 慢 tremolo，再 lowpass 软化
    parts: list[str] = []
    for i, f in enumerate(freqs):
        vol = 0.11 / max(len(freqs), 1) * (1.0 - i * 0.08)
        parts.append(
            f"sine=frequency={f:.3f}:duration={sec:.2f},"
            f"volume={vol:.4f}[s{i}]"
        )
    mix_in = "".join(f"[s{i}]" for i in range(len(freqs)))
    n = len(freqs)
    # anoisesrc 粉噪作气垫
    filter_complex = (
        ";".join(parts)
        + f";anoisesrc=color=pink:duration={sec:.2f}:sample_rate=44100,volume={noise:.4f}[nz]"
        + f";{mix_in}[nz]amix=inputs={n + 1}:duration=longest:dropout_transition=0[m]"
        + f";[m]afade=t=in:st=0:d=1.2,afade=t=out:st={max(sec - 1.8, 0.5):.2f}:d=1.5,"
        f"tremolo=f={lfo:.3f}:d=0.35,lowpass=f=3200,volume=0.55[aout]"
    )

    cmd = [
        executable,
        "-y",
        "-filter_complex",
        filter_complex,
        "-map",
        "[aout]",
        "-t",
        f"{sec:.2f}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "6",
        str(out),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise BgmSynthError("未找到 ffmpeg") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "ffmpeg 失败")[-800:]
        logger.warning("程序化 BGM 生成失败: %s", detail)
        raise BgmSynthError(f"程序化配乐生成失败: {detail}") from exc

    if not out.is_file():
        raise BgmSynthError("配乐文件未写出")
    return out


def public_url(path: Path) -> str:
    return f"/oss/{path.name}"
