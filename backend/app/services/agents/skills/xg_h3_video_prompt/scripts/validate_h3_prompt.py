#!/usr/bin/env python3
"""Validate MiniMax H3 prompt and input counts against bundled official limits.

Used by: xg_h3_video_prompt skill — Step 7 (mechanical validation before delivery).

Checks:
  - prompt character count ≤ 7000
  - duration: integer in [4, 15]
  - ratio: must be W:H or 'adaptive' (adaptive only for non-text-to-video modes)
  - per-mode material count limits (images, videos, audios)
  - timeline endpoints in prompt don't exceed target duration
  - warns on placeholder text like "非完整prompt", "todo", "待补充"

Usage:
  python scripts/validate_h3_prompt.py --input prompt.txt --mode reference-to-video --duration 15 --ratio 16:9 --images 2 --videos 1
  python scripts/validate_h3_prompt.py --input prompt.txt --mode text-to-video --duration 5 --ratio 16:9
  python scripts/validate_h3_prompt.py --input prompt.txt --mode first-last-frame --duration 10 --ratio 9:16 --first-frame --last-frame
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODES = ("text-to-video", "first-last-frame", "reference-to-video", "video-editing")

PROMPT_MAX_CHARS = 7000
DURATION_MIN = 4
DURATION_MAX = 15

MAX_IMAGES_FF = 2          # first-last-frame
MAX_IMAGES_REF = 9         # reference / editing
MAX_VIDEOS = 3
MAX_AUDIOS = 3
MAX_FILES = 12
REF_DURATION_MIN = 2
REF_DURATION_MAX = 15
REF_TOTAL_MAX = 15

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_durations(raw: str | None, label: str, errors: list[str]) -> list[float]:
    """Parse comma-separated duration strings like '3,5,7'."""
    if not raw:
        return []
    result: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = float(part)
        except ValueError:
            errors.append(f"无法解析 {label} 时长: {part}")
            continue
        result.append(val)
    return result


def find_timeline_endpoints(text: str) -> list[float]:
    """Extract timeline endpoint seconds from prompt, e.g. '12-15秒' → 15.0."""
    pattern = re.compile(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|–|—|至|到)\s*(\d+(?:\.\d+)?)\s*(?:s|秒)"
    )
    endpoints: list[float] = []
    for m in pattern.finditer(text):
        try:
            endpoints.append(float(m.group(2)))
        except ValueError:
            pass
    return endpoints


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MiniMax H3 prompt limits")
    parser.add_argument("--input", required=True, help="Path to prompt text file")
    parser.add_argument("--mode", required=True, choices=MODES, help="Generation mode")
    parser.add_argument("--duration", type=int, help="Target duration in seconds")
    parser.add_argument("--ratio", help="Aspect ratio, e.g. 16:9")
    parser.add_argument("--images", type=int, default=0, help="Reference image count")
    parser.add_argument("--videos", type=int, default=0, help="Reference video count")
    parser.add_argument("--audios", type=int, default=0, help="Reference audio count")
    parser.add_argument("--video-durations", help="Comma-separated reference video durations in seconds")
    parser.add_argument("--audio-durations", help="Comma-separated reference audio durations in seconds")
    parser.add_argument("--first-frame", action="store_true", help="First frame image provided")
    parser.add_argument("--last-frame", action="store_true", help="Last frame image provided")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    # --- Read prompt ---
    prompt_path = Path(args.input)
    if not prompt_path.exists():
        errors.append(f"输入文件不存在: {args.input}")
        return _report(errors, warnings, 0, args.json)

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    char_count = len(prompt)

    # --- Prompt checks ---
    if not prompt:
        errors.append("提示词为空")
    if char_count > PROMPT_MAX_CHARS:
        errors.append(f"提示词 {char_count} 字符，超出上限 {PROMPT_MAX_CHARS}")

    # --- Duration ---
    if args.duration is not None:
        if args.duration < DURATION_MIN or args.duration > DURATION_MAX:
            errors.append(f"目标时长 {args.duration}s 不在 {DURATION_MIN}–{DURATION_MAX}s 范围")
        if args.duration != int(args.duration):
            errors.append(f"目标时长必须是整数，当前值: {args.duration}")

    # --- Ratio ---
    valid_ratios = {"16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"}
    if args.ratio:
        if args.mode == "text-to-video" and args.ratio == "adaptive":
            errors.append("文生视频不支持 adaptive 画幅，必须指定具体比例")
        elif args.ratio != "adaptive" and args.ratio not in valid_ratios:
            warnings.append(f"画幅 {args.ratio} 不在常用列表中，请确认 API 是否支持")

    # --- Mode-specific material checks ---
    if args.mode == "text-to-video":
        if args.images > 0 or args.videos > 0 or args.audios > 0:
            errors.append("文生视频模式不支持传入参考素材")
        if args.first_frame or args.last_frame:
            errors.append("文生视频模式不支持首尾帧")

    elif args.mode == "first-last-frame":
        frame_count = (1 if args.first_frame else 0) + (1 if args.last_frame else 0)
        if frame_count > MAX_IMAGES_FF:
            errors.append(f"首尾帧模式最多 {MAX_IMAGES_FF} 张图片，当前: {frame_count}")
        if frame_count == 0:
            warnings.append("未提供首帧或尾帧，将退化为文生视频")
        if args.videos > 0:
            errors.append("首尾帧模式不支持参考视频")
        if args.audios > 0:
            errors.append("首尾帧模式不支持参考音频")

    elif args.mode in ("reference-to-video", "video-editing"):
        if args.images > MAX_IMAGES_REF:
            errors.append(f"参考图片最多 {MAX_IMAGES_REF} 张，当前: {args.images}")
        if args.videos > MAX_VIDEOS:
            errors.append(f"参考视频最多 {MAX_VIDEOS} 段，当前: {args.videos}")
        if args.audios > MAX_AUDIOS:
            errors.append(f"参考音频最多 {MAX_AUDIOS} 段，当前: {args.audios}")

        total_files = args.images + args.videos + args.audios
        if total_files > MAX_FILES:
            errors.append(f"总文件数 {total_files} 超过上限 {MAX_FILES}")

        if args.audios > 0 and args.images == 0 and args.videos == 0:
            errors.append("音频不可单独使用，必须搭配图片或视频")

        if args.mode == "video-editing" and args.videos < 1:
            errors.append("视频编辑模式至少需要 1 段参考视频")

        # Reference durations
        video_durs = parse_durations(args.video_durations, "视频", errors)
        audio_durs = parse_durations(args.audio_durations, "音频", errors)

        for i, d in enumerate(video_durs):
            if d < REF_DURATION_MIN or d > REF_DURATION_MAX:
                errors.append(f"视频 {i+1} 时长 {d}s 不在 {REF_DURATION_MIN}–{REF_DURATION_MAX}s 范围")
        for i, d in enumerate(audio_durs):
            if d < REF_DURATION_MIN or d > REF_DURATION_MAX:
                errors.append(f"音频 {i+1} 时长 {d}s 不在 {REF_DURATION_MIN}–{REF_DURATION_MAX}s 范围")

        if sum(video_durs) > REF_TOTAL_MAX:
            errors.append(f"视频总时长 {sum(video_durs)}s 超过上限 {REF_TOTAL_MAX}s")
        if sum(audio_durs) > REF_TOTAL_MAX:
            errors.append(f"音频总时长 {sum(audio_durs)}s 超过上限 {REF_TOTAL_MAX}s")

    # --- Timeline check ---
    if args.duration is not None:
        endpoints = find_timeline_endpoints(prompt)
        for ep in endpoints:
            if ep > args.duration:
                errors.append(f"提示词时间轴终点 {ep}s 超出目标时长 {args.duration}s")

    # --- Placeholder check ---
    placeholders = ["非完整prompt", "非完整提示词", "todo", "待补充", "可自行补充", "自行发挥"]
    for ph in placeholders:
        if ph in prompt:
            warnings.append(f"提示词包含占位语 '{ph}'，请确认是否已完成")

    return _report(errors, warnings, char_count, args.json)


def _report(errors: list[str], warnings: list[str], char_count: int, as_json: bool) -> int:
    if as_json:
        print(json.dumps({
            "valid": len(errors) == 0,
            "characters": char_count,
            "char_limit": PROMPT_MAX_CHARS,
            "errors": errors,
            "warnings": warnings,
        }, ensure_ascii=False, indent=2))
    else:
        if errors:
            print("FAIL")
        else:
            print("PASS")
        print(f"字符数：{char_count}/{PROMPT_MAX_CHARS}")
        for e in errors:
            print(f"  ERROR: {e}")
        for w in warnings:
            print(f"  WARN: {w}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
