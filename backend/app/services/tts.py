"""剧本台词 → 真实配音。

用 edge-tts（在线、免费、几十种中文嗓音）把每个分镜的台词按角色「绑定音色」
分句合成，拼成一条配音，写回 storyboard.tts_audio_url，供时间线配音轨 / 成片导出使用。

- 合成前对台词跑红线合规（台词会送到在线 TTS，等同送外部服务）。
- 多说话人台词（「名：台词」）按角色音色逐句合成后用 ffmpeg 拼接；只读台词、不读人名。
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import edge_tts
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.models.domain import Character, Episode, Storyboard
from app.services.compliance import check, enforce


# 5 个预设音色 → edge-tts 真实中文嗓音
PRESET_TO_EDGE = {
    "preset_male_young": "zh-CN-YunxiNeural",
    "preset_male_steady": "zh-CN-YunjianNeural",
    "preset_female_sweet": "zh-CN-XiaoyiNeural",
    "preset_female_mature": "zh-CN-XiaoxiaoNeural",
    "preset_old_male": "zh-CN-YunyangNeural",
}
DEFAULT_EDGE_VOICE = "zh-CN-YunyangNeural"  # 旁白 / 未识别说话人


class TTSError(Exception):
    pass


class TTSComplianceBlocked(TTSError):
    def __init__(self, result, enforcement: dict):
        super().__init__("台词触发红线")
        self.result = result
        self.enforcement = enforcement


# 「名：台词」——名字 1~12 个非空白非冒号字符，中英文冒号都认
_SPEAKER_RE = re.compile(r"^\s*([^\s:：]{1,12})\s*[:：]\s*(.+)$")
_STAGE_DIRECTION_RE = re.compile(
    r"（[^（）]*）|\([^()]*\)|【[^【】]*】|\[[^\[\]]*\]"
)


def clean_spoken_text(text: str) -> str:
    """Remove screenplay stage directions while preserving the visible subtitle source."""
    cleaned = text
    # Repeat to handle simple nested/multiple direction groups safely.
    for _ in range(3):
        next_value = _STAGE_DIRECTION_RE.sub("", cleaned)
        if next_value == cleaned:
            break
        cleaned = next_value
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([，。！？、；：,.!?;:])", r"\1", cleaned)
    return cleaned.strip()


def _parse_lines(dialogue: str) -> list[tuple[str | None, str]]:
    """拆成 [(说话人 or None, 台词), ...]；只保留台词正文。"""
    out: list[tuple[str | None, str]] = []
    for raw in dialogue.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _SPEAKER_RE.match(raw)
        if m:
            spoken = clean_spoken_text(m.group(2))
            if spoken:
                out.append((m.group(1).strip(), spoken))
        else:
            spoken = clean_spoken_text(raw)
            if spoken:
                out.append((None, spoken))
    return out


def edge_voice_for_style(voice_style: str | None) -> str:
    # 新绑定直接是 edge ShortName；旧抽象预设走兜底映射
    if voice_style and voice_style.startswith("zh-CN-"):
        return voice_style
    return PRESET_TO_EDGE.get(voice_style or "", DEFAULT_EDGE_VOICE)


async def preview_voice(voice_id: str, text: str | None = None) -> str:
    """合成一小段样音用于试听，返回 /oss 地址。"""
    voice = edge_voice_for_style(voice_id)
    sample = clean_spoken_text((text or "").strip()) or "这是配音试听，台词会用这个声音念出来。"
    sample = sample[:60]
    oss = settings.data_dir / "oss"
    oss.mkdir(parents=True, exist_ok=True)
    name = f"voice_preview_{uuid.uuid4().hex}.mp3"
    await _synth_one(sample, voice, oss / name)
    return "/oss/" + name


def _voice_map(db: Session, drama_id: int | None) -> dict[str, str]:
    """角色名 → edge-tts 嗓音。"""
    if drama_id is None:
        return {}
    chars = db.scalars(
        select(Character).where(Character.drama_id == drama_id, Character.deleted_at.is_(None))
    ).all()
    return {c.name: edge_voice_for_style(c.voice_style) for c in chars if c.name}


async def _synth_one(text: str, voice: str, out_path: Path) -> None:
    # Final safety net: every synthesis path (including future callers) must
    # strip screenplay actions before text is sent to the TTS provider.
    spoken = clean_spoken_text(text)
    if not spoken:
        raise TTSError("台词只包含动作说明，无需配音")
    await edge_tts.Communicate(spoken, voice).save(str(out_path))


def _concat_audio(parts: list[Path], out_path: Path) -> None:
    """多句配音拼成一条；单句直接移动。"""
    if len(parts) == 1:
        parts[0].replace(out_path)
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parts[0].replace(out_path)  # 没 ffmpeg：至少保住第一句，不报错
        return
    cmd = [ffmpeg, "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    labels = "".join(f"[{i}:a]" for i in range(len(parts)))
    cmd += [
        "-filter_complex",
        f"{labels}concat=n={len(parts)}:v=0:a=1[out]",
        "-map",
        "[out]",
        str(out_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=True)


async def synthesize_storyboard_tts(
    db: Session,
    *,
    storyboard_id: int,
    username: str | None = None,
) -> Storyboard:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise LookupError("分镜不存在")
    dialogue = (sb.dialogue or "").strip()
    if not dialogue:
        raise TTSError("该分镜没有台词，无需配音")

    # 合规：台词要送在线 TTS，合成前过红线
    compliance = check(dialogue)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "tts_dialogue")
        raise TTSComplianceBlocked(compliance, audit)

    ep = db.get(Episode, sb.episode_id)
    vmap = _voice_map(db, ep.drama_id if ep else None)
    # 绑定了「说话角色」→ 整条台词都用这个角色的音色（覆盖按名字逐句猜）
    forced_voice: str | None = None
    if sb.speaking_character_id:
        ch = db.get(Character, sb.speaking_character_id)
        if ch is None or ch.deleted_at is not None or (ep and ch.drama_id != ep.drama_id):
            raise TTSError("绑定的说话角色无效，请重新选择")
        if not ch.voice_style:
            raise TTSError(f"角色“{ch.name}”尚未绑定音色，请到「角色资产」一键绑定音频")
        forced_voice = edge_voice_for_style(ch.voice_style)
    else:
        # 未指定说话人：拆镜时尽量自动填；这里再补一次
        try:
            from app.services.storyboard_references import resolve_speaking_character_id, sync_storyboard_characters

            cast = sync_storyboard_characters(db, sb)
            sid = resolve_speaking_character_id(db, sb, cast)
            if sid is not None:
                sb.speaking_character_id = sid
                ch = db.get(Character, sid)
                if ch and ch.voice_style:
                    forced_voice = edge_voice_for_style(ch.voice_style)
                elif ch and not ch.voice_style:
                    raise TTSError(f"角色“{ch.name}”尚未绑定音色，请到「角色资产」一键绑定音频")
                db.commit()
        except TTSError:
            raise
        except Exception:  # noqa: BLE001
            pass
    lines = _parse_lines(dialogue)
    if not lines:
        raise TTSError("台词为空")

    # 多说话人：检查出现的角色是否都有音色
    if not forced_voice and ep is not None:
        missing: list[str] = []
        for speaker, _text in lines:
            if not speaker:
                continue
            if speaker not in vmap or not vmap.get(speaker):
                # vmap 用默认，但角色库里无绑时提醒
                ch_hit = next(
                    (
                        c
                        for c in db.scalars(
                            select(Character).where(
                                Character.drama_id == ep.drama_id,
                                Character.deleted_at.is_(None),
                            )
                        ).all()
                        if c.name == speaker
                    ),
                    None,
                )
                if ch_hit is not None and not (ch_hit.voice_style or "").strip():
                    missing.append(speaker)
        if missing:
            raise TTSError(
                "以下说话人未绑定音色：" + "、".join(dict.fromkeys(missing)) + "。请到「角色资产」一键绑定音频"
            )

    oss = settings.data_dir / "oss"
    oss.mkdir(parents=True, exist_ok=True)
    tmpdir = oss / f"_tts_tmp_{uuid.uuid4().hex}"
    tmpdir.mkdir()
    parts: list[Path] = []
    final_name = f"tts_sb{storyboard_id}_{uuid.uuid4().hex}.mp3"
    final_path = oss / final_name
    try:
        for i, (speaker, text) in enumerate(lines):
            if forced_voice:
                voice = forced_voice
            else:
                voice = vmap.get(speaker, DEFAULT_EDGE_VOICE) if speaker else DEFAULT_EDGE_VOICE
            part = tmpdir / f"{i:03d}.mp3"
            await _synth_one(text, voice, part)
            parts.append(part)
        await asyncio.to_thread(_concat_audio, parts, final_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("配音合成失败 sb=%s: %s", storyboard_id, e)
        raise TTSError(f"配音合成失败：{e}") from e
    finally:
        for p in parts:
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        try:
            tmpdir.rmdir()
        except Exception:  # noqa: BLE001
            pass

    old_audio_url = sb.tts_audio_url
    sb.tts_audio_url = "/oss/" + final_name
    db.commit()
    db.refresh(sb)
    if old_audio_url and old_audio_url.startswith("/oss/") and old_audio_url != sb.tts_audio_url:
        old_path = (settings.data_dir / "oss" / Path(old_audio_url).name).resolve()
        oss_root = (settings.data_dir / "oss").resolve()
        try:
            old_path.relative_to(oss_root)
            old_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
    return sb


async def synthesize_episode_tts(
    db: Session,
    *,
    episode_id: int,
    username: str | None = None,
) -> list[dict]:
    """给一集里所有有台词的分镜配音。单镜失败不中断其他。"""
    rows = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number, Storyboard.id)
    ).all()
    results: list[dict] = []
    for sb in rows:
        if not (sb.dialogue or "").strip():
            continue
        try:
            await synthesize_storyboard_tts(db, storyboard_id=sb.id, username=username)
            results.append({"storyboard_id": sb.id, "status": "completed", "tts_audio_url": sb.tts_audio_url})
        except TTSComplianceBlocked as exc:
            results.append({
                "storyboard_id": sb.id,
                "status": "blocked",
                "banned": exc.enforcement.get("banned"),
                "violation_count": exc.enforcement.get("violation_count"),
            })
        except (TTSError, LookupError) as exc:
            results.append({"storyboard_id": sb.id, "status": "failed", "error": str(exc)})
    return results
