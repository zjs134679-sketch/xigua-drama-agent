"""AI 视频生成服务 —— 分镜图 → 视频片段（ComfyUI 本地节点 / 云端 API）。

支持模式：
- local_comfy: ComfyUI AnimateDiff/SVD 等本地工作流
- cloud_api: 可灵/即梦 等云端 API（预留接口）
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Storyboard, VideoGeneration
from app.services.compliance import FilterResult, check, enforce
from app.services.compute import get_node
from app.services.compute.base import ImageJob


class VideoGenError(Exception):
    pass


class ComplianceBlocked(VideoGenError):
    def __init__(self, result: FilterResult, enforcement: dict):
        super().__init__("内容触发红线")
        self.result = result
        self.enforcement = enforcement


@dataclass
class VideoJob:
    prompt: str
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    reference_images: list[str] | None = None
    duration: int = 4  # seconds
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


VIDEO_DEFAULT_RESOLUTION = "1024x576"
VIDEO_DEFAULT_DURATION = 4
VIDEO_DEFAULT_FPS = 24

# 说话/口型动作线索（有台词的镜头追加到视频提示词）
SPEAKING_CUE = "角色正在说话，嘴部自然开合，有轻微口型变化和自然表情，是对白镜头"


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
    resolution: str = VIDEO_DEFAULT_RESOLUTION,
    extra: str | None = None,
) -> VideoGeneration:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise LookupError("分镜不存在")

    base_prompt = prompt or sb.video_prompt or sb.image_prompt or ""
    # 有台词的镜头追加「说话/口型」线索，让人物在画面里像在说话（即便还没接真口型模型）
    speaking_cue = SPEAKING_CUE if (sb.dialogue or "").strip() else ""
    full_prompt = " ".join(p for p in [base_prompt, speaking_cue, extra] if p)

    compliance = check(full_prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "video_prompt")
        raise ComplianceBlocked(compliance, audit)

    try:
        width, height = [int(x) for x in resolution.split("x")]
    except (ValueError, AttributeError):
        width, height = 1024, 576

    node = get_node(db, node_id)
    if not hasattr(node, "text2video"):
        raise VideoGenError("当前算力节点不支持视频生成（目前仅本地 ComfyUI）")
    audio_driven = reference_mode == "audio_driven"
    if audio_driven and not sb.tts_audio_url:
        raise VideoGenError("当前片段还没有配音，请先绑定角色和音色并生成本片段配音")

    job = VideoJob(
        prompt=full_prompt,
        first_frame_url=sb.composed_image or sb.first_frame_image,
        last_frame_url=sb.last_frame_image,
        duration=duration,
        width=width,
        height=height,
        resolution=resolution,
    )
    if not job.first_frame_url:
        raise VideoGenError("该分镜还没有镜头图，请先在分镜台「出图」")

    # 单图 → 视频：LTX i2v 工作流（提示词合规已在上方校验）。同步等待出片。
    result = await node.text2video(
        input_image=job.first_frame_url,
        input_audio=sb.tts_audio_url if audio_driven else None,
        prompt=full_prompt,
        duration=duration,
        workflow="ltx23-i2v.api.json",
    )
    status = result.status
    # 用本地已去音轨的 /oss 文件做播放地址（与图片素材一致），而不是 ComfyUI /view 原片——
    # 后者带 LTX 模型幻觉音轨，会出现「语音和剧本对不上」。
    video_url = ("/oss/" + Path(result.image_path).name) if result.image_path else result.image_url
    local_path = result.image_path
    error_msg = None if status == "completed" else (result.error or "视频生成失败")

    generation = VideoGeneration(
        storyboard_id=storyboard_id,
        drama_id=sb.episode_id,
        provider=node.type,
        prompt=full_prompt,
        model="LTX-2.3 audio-driven" if audio_driven else model,
        reference_mode=reference_mode,
        first_frame_url=job.first_frame_url,
        last_frame_url=job.last_frame_url,
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
    if video_url and status == "completed":
        sb.video_url = video_url
        generation.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(generation)
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
    resolution: str = VIDEO_DEFAULT_RESOLUTION,
) -> list[dict]:
    """批量提视频生成任务。每个分镜独立提交，失败不中断其他。"""
    results: list[dict] = []
    for sb_id in storyboard_ids:
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
            )
            results.append({
                "storyboard_id": sb_id,
                "status": gen.status,
                "video_url": gen.video_url,
                "video_id": gen.id,
            })
        except ComplianceBlocked as exc:
            results.append({
                "storyboard_id": sb_id,
                "status": "blocked",
                "hits": [hit.__dict__ for hit in exc.result.hits],
            })
        except LookupError as exc:
            results.append({"storyboard_id": sb_id, "status": "failed", "error": str(exc)})
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
    return {
        "id": v.id,
        "storyboard_id": v.storyboard_id,
        "provider": v.provider,
        "prompt": v.prompt,
        "model": v.model,
        "reference_mode": v.reference_mode,
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
