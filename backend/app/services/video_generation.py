"""AI 视频生成服务 —— 多参考图 + 提示词（含角色台词）→ 视频（ComfyUI）。

产品路径（2026）：
- 默认 MiniMax H3 **r2v**（多参考图 ref2va）：角色/场景定妆图作 <Picture N>
- 语音写在提示词里，由视频模型直接生成（无独立 TTS、无分镜出图硬依赖）
- 分镜台只负责拆镜/提示词/台词；出片在成片台
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Episode, Storyboard, VideoGeneration
from app.services.compliance import FilterResult, check, enforce
from app.services.compute import get_node
from app.services.compute.base import ImageJob
from app.services.storyboard_references import (
    _same_continuity_scene,
    resolve_video_reference_images,
)
from app.services.video_frame import extract_video_frame, probe_media_duration_seconds


class VideoGenError(Exception):
    pass


class ComplianceBlocked(VideoGenError):
    def __init__(self, result: FilterResult, enforcement: dict):
        super().__init__("内容触发红线")
        self.result = result
        self.enforcement = enforcement


VIDEO_DEFAULT_RESOLUTION = "1024x576"
# 本地 ComfyUI 单镜图生视频实际上限约 5 秒（与 MiniMax H3 训练区间对齐）。
VIDEO_MIN_DURATION = 2
VIDEO_MAX_DURATION = 5
# 默认 3 秒（H3 帧数约 73，比 5s/124 帧快近一倍）；需要更长可在请求里传 duration=5
VIDEO_DEFAULT_DURATION = 3
VIDEO_DEFAULT_FPS = 24

# 兼容旧 API 参数名 test；实际一律走 final
VIDEO_QUALITY_TEST = "test"
VIDEO_QUALITY_FINAL = "final"
VIDEO_QUALITY_MODES = (VIDEO_QUALITY_FINAL,)

VIDEO_QUALITY_PRESETS: dict[str, dict] = {
    VIDEO_QUALITY_FINAL: {
        # 多参考图 r2v（ref2va 权重）+ 提示词语音
        "workflow": "minimax-h3-r2v.api.json",
        "model_label": "MiniMax-H3 (r2v multi-ref)",
        "resolution": "768x432",
        "supports_audio_driven": False,
        "label_zh": "定稿出片（多参考）",
        "steps": 20,
    },
}


def resolve_video_quality_mode(mode: str | None) -> str:
    """归一化 quality_mode。一律返回 final。"""
    _ = mode
    return VIDEO_QUALITY_FINAL


def minimax_h3_frame_length(duration_sec: int | float, fps: int = 24) -> int:
    """MiniMax H3 length：24fps 下按 17k+5 网格上取整（5s→124 帧）。"""
    a = max(VIDEO_MIN_DURATION, min(float(duration_sec), VIDEO_MAX_DURATION))
    base = max(5, int(round(a * fps)))
    return base + (5 - (base % 17)) % 17

def clamp_video_duration(duration: int | None, default: int = VIDEO_DEFAULT_DURATION) -> int:
    """将请求/分镜时长钳到 ComfyUI 可生成区间 [2, 5] 秒。"""
    if duration is None:
        return default
    try:
        value = int(duration)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return max(VIDEO_MIN_DURATION, min(value, VIDEO_MAX_DURATION))


@dataclass
class VideoJob:
    prompt: str
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    reference_images: list[str] | None = None
    duration: int = VIDEO_DEFAULT_DURATION  # seconds — ComfyUI max 5
    width: int = 1024
    height: int = 576
    seed: int | None = None
    workflow: str = "vid2vid.api.json"
    motion_level: int = 5
    fps: int = 24
    resolution: str = "1024x576"
    aspect_ratio: str = "16:9"


@dataclass
class VideoJobResult:
    status: str  # "completed" | "failed" | "pending" | "processing"
    video_url: str | None = None
    local_path: str | None = None
    error: str | None = None
    task_id: str | None = None
    meta: dict | None = None


# 视频 cue / 负向统一自 negative_packs.yaml
from app.services.negative_packs import (  # noqa: E402
    drama_genre_context as _drama_genre_context,
    video_cue as _video_cue,
    video_negative as _video_negative,
)

SPEAKING_CUE = _video_cue("speaking") or (
    "本镜为对白镜头：画面中已有的主要角色自然说话，嘴部轻微开合与表情变化；"
    "禁止新增无名路人，禁止多人等大并排定装站立；画面中不要出现任何可读文字或字幕"
)
EMPTY_VIDEO_CUE = _video_cue("empty") or (
    "纯环境镜头：只表现建筑、街道、废墟、天空、火光、道具与运镜；画面全程不要出现人物"
)
ANTI_CROWD_CUE = _video_cue("anti_crowd") or (
    "严格遵守首帧人物数量与身份：不要新增角色，不要克隆脸，不要多人等大横排"
)
NO_TEXT_QR_CUE = _video_cue("no_text_qr") or (
    "画面干净：不要字幕、花字、水印、二维码、界面叠加"
)
VIDEO_NEGATIVE_BASE = _video_negative(empty_plate=False)  # 兼容旧测试
VIDEO_NEGATIVE_EMPTY = _video_negative(empty_plate=True)
VIDEO_NEGATIVE_PEOPLE = _video_negative(empty_plate=False)


def strip_spoken_lines_for_video(text: str | None) -> str:
    """清理舞台说明类噪声，但保留可对白表演描述（语音由视频模型生成，不走独立 TTS）。"""
    import re

    value = (text or "").strip()
    if not value:
        return ""
    # 仅去掉括号舞台指示，保留「角色：台词」供模型生成语音
    value = re.sub(r"（[^）]*）|\([^)]*\)|【[^】]*】|\[[^\]]*\]", " ", value)
    value = re.sub(r"[，,]{2,}", "，", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" ，,;；")


def infer_speaking_character_id(db: Session, sb: Storyboard) -> int | None:
    """从台词首行「角色名：」推断说话人，并匹配本剧角色。"""
    import re

    if sb.speaking_character_id:
        return sb.speaking_character_id
    dialogue = extract_dialogue_from_shot_fields(
        sb.dialogue,
        sb.video_prompt,
        sb.action,
    )
    if not dialogue:
        return None
    first = next((ln.strip() for ln in dialogue.splitlines() if ln.strip()), "")
    m = re.match(r"^([^\s:：]{1,12})\s*[:：]", first)
    if not m:
        return None
    name = m.group(1).strip()
    if name in {"旁白", "画外音", "解说", "内心独白"}:
        return None
    episode = db.get(Episode, sb.episode_id) if sb.episode_id else None
    if episode is None:
        return None
    from app.models.domain import Character

    chars = list(
        db.scalars(
            select(Character).where(
                Character.drama_id == episode.drama_id,
                Character.deleted_at.is_(None),
            )
        ).all()
    )
    # 精确名优先，再前缀/包含
    for c in chars:
        if (c.name or "").strip() == name:
            return c.id
    for c in chars:
        n = (c.name or "").strip()
        if n and (name in n or n in name):
            return c.id
    return None


def extract_dialogue_from_shot_fields(
    dialogue: str | None,
    *extra_fields: str | None,
) -> str:
    """分镜 dialogue 为空时，从 video_prompt/action 等字段捞「角色：台词」行。

    很多旧分镜把对白只写在提示词里，导致成片无台词约束、模型乱说。
    """
    import re

    primary = (dialogue or "").strip()
    if primary:
        return primary

    lines: list[str] = []
    seen: set[str] = set()
    # 角色名：台词 / 旁白：……
    pat = re.compile(
        r"(?:^|[\n；;。])\s*"
        r"([\u4e00-\u9fffA-Za-z0-9_·]{1,12})\s*[:：]\s*"
        r"([^\n；;]{1,80})"
    )
    for field in extra_fields:
        text = (field or "").strip()
        if not text:
            continue
        for m in pat.finditer("\n" + text):
            name, content = m.group(1).strip(), m.group(2).strip().strip("「」\"'“”")
            if not content:
                continue
            # 过滤明显非对白的标签
            if name in {"景别", "机位", "运镜", "动作", "气氛", "时长", "秒", "画面", "视觉"}:
                continue
            if re.fullmatch(r"\d+[-–~到至]\d+秒?", name):
                continue
            key = f"{name}:{content}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{name}：{content}")
    return "\n".join(lines[:6])


def dialogue_for_model_audio(
    dialogue: str | None,
    *,
    speaker_name: str | None = None,
    picture_tag: str | None = None,
    voiceover: bool = False,
) -> str | None:
    """把分镜台词整理成 H3 可生成的【必须照念】语音提示。

    关键：必须写入具体台词；禁止只写「自然说话」否则模型会即兴乱说。
    画面仍禁止烧字幕。
    """
    import re

    text = (dialogue or "").strip()
    if not text:
        return None
    # 去掉舞台指示，保留「角色：台词」
    text = re.sub(r"（[^）]*）|\([^)]*\)|【[^】]*】|\[[^\]]*\]", "", text)
    lines: list[str] = []
    for raw in text.splitlines():
        raw = raw.strip().strip("「」\"'“”")
        if not raw:
            continue
        # 旁白：…… / 画外音：……
        m_vo = re.match(r"^(旁白|画外音|解说|内心独白)\s*[:：]\s*(.+)$", raw)
        if m_vo:
            content = m_vo.group(2).strip()
            if content:
                lines.append(f"画外音照念：「{content[:100]}」")
            continue
        m = re.match(r"^([^\s:：]{1,12})\s*[:：]\s*(.+)$", raw)
        if m:
            name, content = m.group(1).strip(), m.group(2).strip()
            if content:
                lines.append(f"{name}清晰说：「{content[:100]}」")
        else:
            who = "画外音" if voiceover else (speaker_name or "角色")
            if who in ("旁白", "画外音", "解说"):
                lines.append(f"画外音照念：「{raw[:100]}」")
            else:
                lines.append(f"{who}清晰说：「{raw[:100]}」")
    if not lines:
        return None
    spoken = "；".join(lines)[:320]
    who_ref = f"（说话人对应参考 {picture_tag}）" if picture_tag and not voiceover else ""
    if voiceover:
        return (
            f"【必须照念的音频·禁止改写/即兴/翻译】{spoken}。"
            "仅播放该段画外音，语气自然；画面角色可不张嘴；"
            "禁止胡言乱语、禁止额外对白、禁止英文乱说；"
            "画面中不要出现任何字幕、花字或对白文字。"
        )
    return (
        f"【必须照念的对白·禁止改写/即兴/翻译】{spoken}{who_ref}。"
        "口型与上述对白严格同步，只说这些字、一字不改、不多说一句；"
        "禁止胡言乱语、禁止额外台词、禁止英文乱说、禁止哼唱替代对白；"
        "由视频模型直接生成清晰中文语音；画面中不要出现任何字幕、花字或对白文字。"
    )


# 无对白时强制静音，避免模型因「natural speech」乱编台词
SILENCE_AUDIO_CUE = (
    "【声音】本镜无对白：角色闭嘴不说话、无念白、无解说、无哼唱；"
    "仅允许环境音与动作音效（如脚步、纸页、门铃），不要生成任何可听清的语句。"
    " no dialogue, no speech, no talking, closed mouth, ambient foley only."
)


def build_picture_binding_prompt(ref_labels: list[str]) -> str:
    """根据参考图顺序生成 <Picture N> 绑定说明。"""
    parts: list[str] = []
    for i, label in enumerate(ref_labels, start=1):
        tag = f"<Picture {i}>"
        lab = (label or f"参考{i}").strip()
        if lab.startswith("场景") or "场景" in lab:
            parts.append(f"{tag} 是场景/环境参考（{lab}）：锁定建筑、光线、空间，忽略参考图中多余路人。")
        elif lab.startswith("角色") or "角色" in lab:
            parts.append(f"{tag} 是角色定妆参考（{lab}）：锁定脸、发型、服装与体型身份。")
        else:
            parts.append(f"{tag} 是视觉参考（{lab}）。")
    return " ".join(parts)


def _linked_characters(db: Session, sb: Storyboard) -> list:
    from app.models.domain import Character, StoryboardCharacter

    ids = list(
        db.scalars(
            select(StoryboardCharacter.character_id).where(
                StoryboardCharacter.storyboard_id == sb.id
            )
        ).all()
    )
    if not ids:
        return []
    return list(
        db.scalars(
            select(Character).where(Character.id.in_(ids), Character.deleted_at.is_(None))
        ).all()
    )


def build_video_prompt_parts(
    db: Session,
    sb: Storyboard,
    *,
    base_prompt: str,
    frame_source: str,
    extra: str | None = None,
    style_cue: str | None = None,
    picture_bindings: str | None = None,
    speaker_picture_tag: str | None = None,
) -> tuple[str, str, bool, bool]:
    """组装成片提示词。返回 (full_prompt, negative, empty_plate, has_speech)。

    r2v：先绑定 <Picture N>，再写镜头/动作，最后写「角色说：台词」供模型生成语音。
    """
    from app.services.asset_generation import (
        has_character_speech_lines,
        is_environment_only_shot,
        is_voiceover_only_dialogue,
        scrub_people_from_prompt,
        strip_on_screen_text_terms,
    )

    cast = _linked_characters(db, sb)
    empty_plate = is_environment_only_shot(sb, cast)
    dialogue = extract_dialogue_from_shot_fields(
        sb.dialogue,
        sb.video_prompt,
        sb.action,
        sb.image_prompt,
        sb.description,
    )
    # 捞到台词且库里为空：回写分镜，后续审核/再出片可复用
    if dialogue and not (sb.dialogue or "").strip():
        sb.dialogue = dialogue
    # 有「角色名：台词」且非纯旁白 → 口型对白；任意非空台词 → 都要照念音频
    vo_only = bool(dialogue) and is_voiceover_only_dialogue(dialogue)
    has_lip_speech = (
        bool(dialogue)
        and has_character_speech_lines(dialogue)
        and not vo_only
        and not empty_plate
    )
    has_spoken_audio = bool(dialogue)  # 分镜写了台词就必须照念，避免模型乱编
    has_speech = has_lip_speech  # 对外：是否「角色张嘴说话」镜头

    prompt = strip_on_screen_text_terms(base_prompt) or ""
    prompt = strip_spoken_lines_for_video(prompt)
    if empty_plate:
        prompt = scrub_people_from_prompt(prompt) or prompt

    speaking_cue = SPEAKING_CUE if has_lip_speech else ""
    speaker_name = None
    if sb.speaking_character_id:
        from app.models.domain import Character

        speaker = db.get(Character, sb.speaking_character_id)
        if speaker is not None and speaker.name:
            speaker_name = speaker.name

    model_audio_cue = ""
    silence_cue = ""
    if has_spoken_audio:
        model_audio_cue = (
            dialogue_for_model_audio(
                dialogue,
                speaker_name=speaker_name,
                picture_tag=speaker_picture_tag if has_lip_speech else None,
                voiceover=vo_only or empty_plate,
            )
            or ""
        )
    else:
        # 无台词时禁止 natural speech，否则 H3 会即兴乱说
        silence_cue = SILENCE_AUDIO_CUE

    continuity_cue = ""
    if frame_source.startswith("prev_"):
        if empty_plate:
            continuity_cue = (
                "镜头运动与上一镜连续，保持同一场景空间、建筑与光线；"
                "若上一帧含人物则忽略其人，只延续环境运镜"
            )
        else:
            continuity_cue = (
                "镜头运动与上一镜连续，保持同一主要角色外貌、服装、场景与光线，承接上一镜继续运镜；"
                "不要额外增加人物"
            )

    empty_cue = EMPTY_VIDEO_CUE if empty_plate else ""
    anti_crowd = "" if empty_plate else ANTI_CROWD_CUE
    speaker_cue = ""
    if has_lip_speech and speaker_name:
        tag = f"（{speaker_picture_tag}）" if speaker_picture_tag else ""
        speaker_cue = (
            f"本镜说话人是{speaker_name}{tag}：以{speaker_name}为口型与表演主体；"
            "其他角色可听讲或反应，不要所有人同时大幅度张嘴；"
            "不要把对话内容写成画面字幕"
        )

    # 镜头元数据（景别/运镜/动作）补充进提示词，分镜出图取消后这些字段更重要
    shot_bits = []
    if sb.shot_type:
        shot_bits.append(f"景别{sb.shot_type}")
    if sb.angle:
        shot_bits.append(f"机位{sb.angle}")
    if sb.movement:
        shot_bits.append(f"运镜{sb.movement}")
    if sb.action and (sb.action or "").strip() not in (prompt or ""):
        shot_bits.append(f"动作：{(sb.action or '').strip()[:120]}")
    if sb.atmosphere:
        shot_bits.append(f"气氛{sb.atmosphere}")
    shot_cue = "，".join(shot_bits)

    extra_clean = strip_spoken_lines_for_video(strip_on_screen_text_terms(extra) or "")
    # 对白/静音放在靠后高优先级位置，压过前面的画面描述
    parts = [
        picture_bindings or "",
        prompt,
        shot_cue,
        speaking_cue,
        speaker_cue,
        continuity_cue,
        empty_cue,
        anti_crowd,
        NO_TEXT_QR_CUE,
        style_cue or "",
        extra_clean,
        model_audio_cue,
        silence_cue,
        "no subtitles, no on-screen text, no watermark, no qr code",
    ]
    if has_spoken_audio:
        parts.append("spoken audio must match the scripted lines exactly")
    else:
        parts.append("no spoken words, no dialogue track")
    if empty_plate:
        parts.append("no people, no person, no human figures, empty environment only")

    full = " ".join(p.strip() for p in parts if p and str(p).strip())
    negative = VIDEO_NEGATIVE_EMPTY if empty_plate else VIDEO_NEGATIVE_PEOPLE
    return full, negative, empty_plate, has_speech


def _build_storyboard_video_desc(sb: Storyboard, reference_mode: str = "single") -> str:
    """从分镜信息组装中文视频描述。"""
    parts = []
    if sb.shot_type:
        parts.append(f"景别：{sb.shot_type}")
    if sb.angle:
        parts.append(f"机位角度：{sb.angle}")
    if sb.movement:
        parts.append(f"镜头运动：{sb.movement}")
    if sb.action:
        parts.append(f"动作：{sb.action}")
    if sb.atmosphere:
        parts.append(f"气氛：{sb.atmosphere}")
    if sb.image_prompt:
        parts.append(f"视觉参考：{sb.image_prompt}")

    desc = "，".join(parts)
    if sb.dialogue:
        desc += f"。台词：{sb.dialogue}"
    return desc


async def generate_video_prompt(
    sb: Storyboard,
    model: str = "kling",
    mode: str = "multi_ref",
    dialogue: str | None = None,
) -> str:
    """据分镜信息 + 模型类型，生成对应格式的视频提示词。

    mode 说明：
    - multi_ref: 通用多参模式（含资产/分镜图引用 @图N）
    - first_last: 首尾帧模式（纯文本描述）
    - seedance2: 即梦2.0 结构化格式
    - wan2: 万象2.6 叙事式中文
    """
    desc = _build_storyboard_video_desc(sb)
    prompt = sb.video_prompt or sb.image_prompt or ""

    if mode == "multi_ref":
        return _prompt_multi_ref(desc, prompt, dialogue)
    elif mode == "first_last":
        return _prompt_first_last(desc, prompt, dialogue)
    elif mode == "seedance2":
        return _prompt_seedance2(desc, prompt, sb.duration or VIDEO_DEFAULT_DURATION, dialogue)
    elif mode == "wan2":
        return _prompt_wan2(desc, prompt, dialogue)
    else:
        return prompt or desc


def _prompt_multi_ref(desc: str, prompt: str, dialogue: str | None) -> str:
    p = f"【生成指令】\n根据以下分镜参考生成视频：\n{desc}\n"
    if dialogue:
        p += f"台词：{dialogue}\n"
    if prompt:
        p += f"\n视觉风格参考：{prompt}"
    return p


def _prompt_first_last(desc: str, prompt: str, dialogue: str | None) -> str:
    p = (
        f"【画面】\n{desc}\n{prompt}\n"
        f"【运动】\n0秒到4秒：连续自然运动，镜头平滑。\n"
    )
    if dialogue:
        p += f"【声音】\n{dialogue}（对白，启用口型动作）\n"
    else:
        p += "【声音】\n无对白，保留环境气氛。\n"
    p += "【镜头】\n电影感，单镜头连续拍摄，不切镜。\n【叙事】\n按描述推进。"
    return p


def _prompt_seedance2(desc: str, prompt: str, duration: int, dialogue: str | None) -> str:
    ms = max(duration * 1000, 1000)
    p = f"生成由以下1个镜头组成的视频：\n\n镜头1<duration-ms>{ms}</duration-ms>：{desc}"
    if dialogue:
        p += f"。角色说：{dialogue}"
    else:
        p += "。无对白。"
    p += f". {prompt}"
    return p


def _prompt_wan2(desc: str, prompt: str, dialogue: str | None) -> str:
    p = (
        f"电影感短剧镜头。\n{desc}\n{prompt}\n"
        "连续单镜头拍摄，镜头稳定。\n"
    )
    if dialogue:
        p += f"对白：{dialogue}\n"
    else:
        p += "无对白。\n"
    return p


def _previous_continuous_shot(db: Session, sb: Storyboard) -> Storyboard | None:
    """同集、镜号更小、且场景连续的最近一镜。"""
    rows = db.scalars(
        select(Storyboard)
        .where(
            Storyboard.episode_id == sb.episode_id,
            Storyboard.deleted_at.is_(None),
            Storyboard.storyboard_number < sb.storyboard_number,
        )
        .order_by(Storyboard.storyboard_number.desc())
    ).all()
    for prev in rows:
        # 同一运镜段落优先；否则按场景连续性判断
        if sb.segment_key and prev.segment_key == sb.segment_key:
            return prev
        if _same_continuity_scene(prev, sb):
            return prev
        break
    return None


def resolve_i2v_start_frame(db: Session, sb: Storyboard, *, use_prev_last_frame: bool = True) -> tuple[str | None, str]:
    """决定图生视频的起始帧。

    优先级（避免「中段/落幅一出视频又变成起幅」）：
    1. 本镜 **composed_image**（分镜台为本镜单独出的图）——永远优先
    2. 同段落/连续镜的上一镜 **视频尾帧**（仅本镜还没有独立合成图时）
    3. 上一镜成图 / 本镜 first_frame 兜底

    说明：拆镜后各子镜可能共享 first_frame（拆前原图）。若误用「上一镜成图」
    覆盖本镜合成图，#07 会从 #06 起幅图 i2v，看起来像又渲染成了第一镜。
    """
    # 本镜正式出图：必须作为 i2v 起点
    if sb.composed_image:
        return sb.composed_image, "own_image"

    own_fallback = sb.first_frame_image
    if use_prev_last_frame:
        prev = _previous_continuous_shot(db, sb)
        if prev is not None:
            same_segment = bool(sb.segment_key and prev.segment_key == sb.segment_key)
            # 仅当本镜没有独立合成图时，才用上一镜尾帧/成图衔接
            if prev.last_frame_image and (same_segment or not own_fallback):
                return prev.last_frame_image, f"prev_last_frame:{prev.storyboard_number}"
            if same_segment and (prev.composed_image or prev.first_frame_image):
                return (
                    prev.composed_image or prev.first_frame_image,
                    f"prev_segment_image:{prev.storyboard_number}",
                )
            if not own_fallback and (prev.composed_image or prev.first_frame_image):
                return (
                    prev.composed_image or prev.first_frame_image,
                    f"prev_image:{prev.storyboard_number}",
                )
    if own_fallback:
        return own_fallback, "own_first_frame"
    return None, "none"


async def submit_video_generation(
    db: Session,
    *,
    storyboard_id: int,
    prompt: str | None = None,
    model: str = "default",
    reference_mode: str = "single",
    node_id: int | None = None,
    username: str | None = None,
    duration: int = VIDEO_DEFAULT_DURATION,
    resolution: str | None = None,
    extra: str | None = None,
    use_prev_last_frame: bool = True,
    quality_mode: str = VIDEO_QUALITY_FINAL,
) -> VideoGeneration:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise LookupError("分镜不存在")

    # 台词回填 + 说话人自动绑定（成片语音/<Picture N> 依赖这两项）
    filled = extract_dialogue_from_shot_fields(
        sb.dialogue, sb.video_prompt, sb.action, sb.image_prompt, sb.description
    )
    dirty = False
    if filled and not (sb.dialogue or "").strip():
        sb.dialogue = filled
        dirty = True
    speaker_id = infer_speaking_character_id(db, sb)
    if speaker_id and sb.speaking_character_id != speaker_id:
        sb.speaking_character_id = speaker_id
        dirty = True
    if dirty:
        db.flush()

    # 默认 MiniMax H3 图生视频（旧 test 参数回落 final）
    quality = resolve_video_quality_mode(quality_mode)
    ref_mode = (reference_mode or "single").strip().lower()
    # 显式 audio_driven；「自动对口型」在判断空镜/真人对白之后再打开
    audio_driven = ref_mode == "audio_driven"
    preset = VIDEO_QUALITY_PRESETS[quality]
    workflow = str(preset["workflow"])
    model_label = str(preset["model_label"])
    if not resolution or not str(resolution).strip():
        resolution = str(preset["resolution"])
    else:
        resolution = str(resolution).strip()

    # 按「视频」能力选节点（云 Seedance / 本地 Comfy）
    node = get_node(db, node_id, capability="video")
    ms = getattr(node, "model_settings", None) or {}

    # 请求未传或传 0 时，回落分镜时长，再钳到 ComfyUI 上限（5s）
    duration = clamp_video_duration(
        duration if duration and duration > 0 else (sb.duration or VIDEO_DEFAULT_DURATION)
    )

    # 多参考图：角色定妆（含上传）+ 场景 —— 必须在选工作流之前算好
    ref_rows, ref_source = resolve_video_reference_images(db, sb, max_refs=6)
    ref_urls = [r.url for r in ref_rows if r.url]
    ref_labels = [r.label for r in ref_rows]
    picture_bindings = build_picture_binding_prompt(ref_labels) if ref_labels else ""
    has_character_ref = any(r.kind == "character" for r in ref_rows)

    # 说话人对应的 <Picture N>（人物在列表前部）
    speaker_picture_tag: str | None = None
    if sb.speaking_character_id and ref_rows:
        from app.models.domain import Character

        speaker = db.get(Character, sb.speaking_character_id)
        sname = (speaker.name if speaker else "") or ""
        for i, row in enumerate(ref_rows, start=1):
            if row.kind == "character" and sname and sname in (row.label or ""):
                speaker_picture_tag = f"<Picture {i}>"
                break
    if speaker_picture_tag is None:
        for i, row in enumerate(ref_rows, start=1):
            if row.kind == "character":
                speaker_picture_tag = f"<Picture {i}>"
                break

    # 有人物/多参考 → 强制 r2v，避免算力节点仍配旧 i2v 丢掉上传角色图
    if isinstance(ms, dict):
        override = (
            ms.get("workflow_i2v")
            or ms.get("workflow_video")
            or ms.get("i2v_workflow")
            or ms.get("workflow_r2v")
        )
        if isinstance(override, str) and override.strip():
            workflow = override.strip()
    if has_character_ref or len(ref_urls) >= 2:
        # 旧 i2v 单图无法吃多角色定妆
        if "i2v" in workflow.lower() and "r2v" not in workflow.lower():
            workflow = "minimax-h3-r2v.api.json"
        elif "r2v" not in workflow.lower() and "ltx" not in workflow.lower():
            workflow = "minimax-h3-r2v.api.json"
        model_label = "MiniMax-H3 (r2v multi-ref)"
    elif isinstance(ms, dict):
        if "minimax" in workflow.lower() or "h3" in workflow.lower():
            model_label = "MiniMax-H3 (node)"
        elif "ltx" in workflow.lower():
            model_label = "LTX-2.3 (node)"
    if getattr(node, "type", None) == "cloud_api":
        provider = getattr(node, "provider", "") or ""
        model_label = f"云API/{provider}" if provider else "云API"

    is_r2v = "r2v" in workflow.lower() or "reference" in workflow.lower()
    start_frame, frame_source = None, f"refs:{ref_source}"
    if not is_r2v:
        # 单图 i2v：优先用第一个角色定妆，而不是场景/空镜
        char_first = next((r.url for r in ref_rows if r.kind == "character" and r.url), None)
        if char_first:
            start_frame, frame_source = char_first, "character_portrait"
        else:
            start_frame, frame_source = resolve_i2v_start_frame(
                db, sb, use_prev_last_frame=use_prev_last_frame and not audio_driven
            )
    elif not ref_urls:
        start_frame, frame_source = resolve_i2v_start_frame(
            db, sb, use_prev_last_frame=False
        )
        if start_frame:
            ref_urls = [start_frame]
            ref_labels = ["本镜参考"]
            picture_bindings = build_picture_binding_prompt(ref_labels)
            frame_source = f"fallback_single:{frame_source}"

    # 项目 Style Bible / 画风：注入视频阶段风格段
    style_cue = ""
    try:
        from app.models.domain import ArtStyle, Drama, Episode
        from app.services.art_style_pack import style_video_tags
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_VIDEO

        episode = db.get(Episode, sb.episode_id) if sb.episode_id else None
        drama = db.get(Drama, episode.drama_id) if episode is not None else None
        contract = compose(db=db, drama=drama, stage=STAGE_VIDEO)
        style_cue = contract.image_constraint_block() or contract.video_tags or contract.prompt_suffix or ""
        if not style_cue:
            style_name = (drama.style if drama else None) or ""
            if style_name.strip():
                style = db.scalars(
                    select(ArtStyle).where(ArtStyle.name == style_name.strip())
                ).first()
                if style is not None:
                    style_cue = style_video_tags(db, style, prefer="zh") or style.prompt_suffix or ""
    except Exception:
        style_cue = ""

    from app.services.prompt_orchestrate import align_video_prompt_to_duration, orchestrate_video_prompt

    # 与即将送 Comfy 的 duration 一致（已钳 2–5s）
    raw_base = prompt or sb.video_prompt or sb.image_prompt or ""
    # 先用 xg_h3_video_prompt 技能编排（H3 提示词工程方法论）
    ctx = ", ".join(
        v.strip() for v in (
            sb.title, sb.location, sb.time, sb.shot_type, sb.angle,
            sb.movement, sb.action, sb.atmosphere, sb.dialogue,
        ) if v and v.strip()
    )
    base_prompt = orchestrate_video_prompt(
        db,
        raw_prompt=raw_base,
        context=ctx,
        duration_sec=int(duration),
        aspect_ratio="16:9",
        mode="reference-to-video" if (ref_urls or start_frame) else "text-to-video",
        image_count=len(ref_urls) if ref_urls else (1 if start_frame else 0),
        has_dialogue=bool(sb.dialogue and sb.dialogue.strip()),
    )
    base_prompt = align_video_prompt_to_duration(base_prompt, int(duration))
    if not prompt and sb.video_prompt and base_prompt != (sb.video_prompt or "").strip():
        sb.video_prompt = base_prompt
    full_prompt, video_negative, empty_plate, has_speech = build_video_prompt_parts(
        db,
        sb,
        base_prompt=base_prompt,
        frame_source=frame_source,
        extra=extra,
        style_cue=style_cue,
        picture_bindings=picture_bindings,
        speaker_picture_tag=speaker_picture_tag,
    )
    # 题材感知负向（覆盖 build 内默认）
    try:
        ep = db.get(Episode, sb.episode_id) if sb.episode_id else None
        g, n = _drama_genre_context(db, ep.drama_id if ep else None)
        video_negative = _video_negative(empty_plate=empty_plate, genre=g, narrative=n) or video_negative
    except Exception:  # noqa: BLE001
        pass

    # 已取消独立 TTS：对白写入视频提示词，由视频模型直接生成语音
    audio_driven = False
    if ref_mode == "audio_driven":
        ref_mode = "multi_ref" if is_r2v else "single"
        reference_mode = ref_mode
    if is_r2v:
        reference_mode = "multi_ref"
        ref_mode = "multi_ref"

    compliance = check(full_prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "video_prompt")
        raise ComplianceBlocked(compliance, audit)

    try:
        width, height = [int(x) for x in resolution.split("x")]
    except (ValueError, AttributeError):
        width, height = (768, 432)
        resolution = f"{width}x{height}"

    if not hasattr(node, "text2video"):
        raise VideoGenError(
            "当前算力不支持出视频：请启动本机 ComfyUI，并在「算力」页确认节点在线（出片走 Comfy，不走云 API）"
        )
    tts_duration: float | None = None
    audio_warning: str | None = None

    job = VideoJob(
        prompt=full_prompt,
        first_frame_url=start_frame or (ref_urls[0] if ref_urls else None),
        last_frame_url=sb.last_frame_image,
        reference_images=ref_urls or None,
        duration=duration,
        width=width,
        height=height,
        resolution=resolution,
        workflow=workflow,
    )
    if not ref_urls and not job.first_frame_url:
        raise VideoGenError(
            "没有可用参考图：请先在角色/场景资产台生成或上传定妆图，"
            "或在分镜检视里上传本镜参考图（已取消「分镜出图」依赖）"
        )

    # 多参考 / 单图 → 视频；语音在提示词中由模型生成
    t2v_kwargs = {
        "input_image": job.first_frame_url,
        "input_audio": None,
        "prompt": full_prompt,
        "negative": video_negative,
        "duration": duration,
        "workflow": workflow,
        "width": width,
        "height": height,
    }
    # 新签名支持 reference_images；旧节点忽略
    try:
        result = await node.text2video(**t2v_kwargs, reference_images=ref_urls or None)
    except TypeError:
        result = await node.text2video(**t2v_kwargs)
    status = result.status
    # 保留模型原生音轨的 /oss 成片路径
    video_url = ("/oss/" + Path(result.image_path).name) if result.image_path else result.image_url
    local_path = result.image_path
    error_msg = None if status == "completed" else (result.error or "视频生成失败")

    last_frame_url = sb.last_frame_image
    if video_url and status == "completed":
        extracted = extract_video_frame(local_path or video_url, position="last", prefix="xigua_last")
        if extracted:
            last_frame_url = extracted
            # 只记在本镜 last_frame；衔接时由 resolve_i2v_start_frame 读「上一镜尾帧」
            # 切勿写入下一镜 first_frame/composed_image，否则：
            # 1) 成片台勾选 2 镜出片，第 3 镜也会被标成「有图」（假出图）
            # 2) r2v 多参考会把尾帧当成「本镜参考」多塞一张图
            sb.last_frame_image = extracted

    # model 字段记录档位，便于成片台区分测试片 / 定稿片
    stored_model = f"{model_label} audio-driven" if audio_driven else model_label

    generation = VideoGeneration(
        storyboard_id=storyboard_id,
        drama_id=sb.episode_id,
        provider=node.type,
        prompt=full_prompt,
        model=stored_model,
        reference_mode=f"{quality}|{reference_mode}|start={frame_source}",
        first_frame_url=job.first_frame_url,
        last_frame_url=last_frame_url,
        duration=duration,
        fps=VIDEO_DEFAULT_FPS,
        resolution=resolution,
        aspect_ratio=job.aspect_ratio,
        motion_level=job.motion_level,
        video_url=video_url or None,
        local_path=local_path or None,
        status=status,
        error_msg=error_msg,
        width=width,
        height=height,
    )
    db.add(generation)
    # 分镜时长若仍是旧的 10–15s，写回实际出片时长，与 ComfyUI 一致
    if not sb.duration or sb.duration > duration:
        sb.duration = duration
    if video_url and status == "completed":
        sb.video_url = video_url
        generation.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(generation)
    # 动态属性：API 层读取后塞进 JSON（不落库）
    setattr(generation, "audio_warning", audio_warning)
    setattr(generation, "tts_duration", tts_duration)
    setattr(generation, "kept_embedded_audio", bool(audio_driven and status == "completed"))
    setattr(generation, "quality_mode", quality)
    return generation


async def submit_batch_video_generation(
    db: Session,
    *,
    storyboard_ids: list[int],
    model: str = "default",
    reference_mode: str = "single",
    node_id: int | None = None,
    username: str | None = None,
    duration: int = VIDEO_DEFAULT_DURATION,
    resolution: str | None = None,
    use_prev_last_frame: bool = True,
    quality_mode: str = VIDEO_QUALITY_FINAL,
) -> list[dict]:
    """批量提视频生成任务。每个分镜独立提交，失败不中断其他。

    按镜号顺序处理时，use_prev_last_frame 可让后镜吃到前镜刚抽出的尾帧。
    """
    results: list[dict] = []
    # 按镜号顺序，确保尾帧衔接
    ordered_ids = list(storyboard_ids)
    try:
        rows = db.scalars(select(Storyboard).where(Storyboard.id.in_(storyboard_ids))).all()
        order_map = {row.id: row.storyboard_number for row in rows}
        ordered_ids = sorted(storyboard_ids, key=lambda i: order_map.get(i, 10**9))
    except Exception:  # noqa: BLE001
        ordered_ids = list(storyboard_ids)

    for sb_id in ordered_ids:
        try:
            gen = await submit_video_generation(
                db,
                storyboard_id=sb_id,
                model=model,
                reference_mode=reference_mode,
                node_id=node_id,
                username=username,
                duration=duration,
                resolution=resolution,
                use_prev_last_frame=use_prev_last_frame,
                quality_mode=quality_mode,
            )
            results.append({
                "storyboard_id": sb_id,
                "status": gen.status,
                "video_url": gen.video_url,
                "video_id": gen.id,
                "quality_mode": getattr(gen, "quality_mode", quality_mode),
                "model": gen.model,
                "error": gen.error_msg,
            })
        except ComplianceBlocked as exc:
            results.append({
                "storyboard_id": sb_id,
                "status": "blocked",
                "hits": [hit.__dict__ for hit in exc.result.hits],
            })
        except (LookupError, VideoGenError) as exc:
            results.append({"storyboard_id": sb_id, "status": "failed", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            results.append({"storyboard_id": sb_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return results


def poll_video_status(db: Session, video_id: int) -> dict:
    gen = db.get(VideoGeneration, video_id)
    if gen is None:
        return {"video_id": video_id, "status": "not_found"}
    return {
        "video_id": gen.id,
        "storyboard_id": gen.storyboard_id,
        "status": gen.status,
        "video_url": gen.video_url,
        "local_path": gen.local_path,
        "error_msg": gen.error_msg,
        "duration": gen.duration,
        "created_at": gen.created_at.isoformat() if gen.created_at else None,
        "completed_at": gen.completed_at.isoformat() if gen.completed_at else None,
    }


def batch_poll_video_status(db: Session, video_ids: list[int]) -> list[dict]:
    return [poll_video_status(db, vid) for vid in video_ids]


def list_storyboard_videos(db: Session, storyboard_id: int) -> list[dict]:
    rows = db.scalars(
        select(VideoGeneration)
        .where(VideoGeneration.storyboard_id == storyboard_id, VideoGeneration.deleted_at.is_(None))
        .order_by(VideoGeneration.id.desc())
    ).all()
    return [_video_view(v) for v in rows]


def _video_view(v: VideoGeneration) -> dict:
    # reference_mode 形如 test|single|start=... 或 final|audio_driven|start=...
    ref = v.reference_mode or ""
    quality = ref.split("|", 1)[0] if ref else None
    if quality not in VIDEO_QUALITY_MODES:
        quality = None
    return {
        "id": v.id,
        "storyboard_id": v.storyboard_id,
        "provider": v.provider,
        "prompt": v.prompt,
        "model": v.model,
        "reference_mode": v.reference_mode,
        "quality_mode": quality,
        "video_url": v.video_url,
        "local_path": v.local_path,
        "status": v.status,
        "duration": v.duration,
        "resolution": v.resolution,
        "error_msg": v.error_msg,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "completed_at": v.completed_at.isoformat() if v.completed_at else None,
    }


def delete_video(db: Session, video_id: int) -> dict:
    gen = db.get(VideoGeneration, video_id)
    if gen is None or gen.deleted_at is not None:
        return {"deleted": False, "error": "视频不存在"}
    gen.deleted_at = datetime.utcnow()
    # 如果分镜绑定这个视频，清除
    if gen.storyboard_id:
        sb = db.get(Storyboard, gen.storyboard_id)
        if sb and sb.video_url == gen.video_url:
            sb.video_url = None
    db.commit()
    return {"deleted": True, "video_id": video_id}
