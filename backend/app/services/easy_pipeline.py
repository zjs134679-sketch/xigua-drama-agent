"""小白一键出片编排（西瓜自研）。

从「有剧本/小说」到「导出 MP4」的可续跑流水线，不依赖第三方产品流程或文案。
每步可单独失败并写进 progress，支持 skip_existing 跳过已完成素材。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.models.domain import Character, Drama, Episode, ProductionJob, Scene, Storyboard
from app.services.agents.extract_agent import extract, save_extracted
from app.services.agents.script_agent import generate_script
from app.services.agents.storyboard_agent import break_storyboards, save_storyboards
from app.services.asset_generation import (
    AssetGenerationError,
    ComplianceBlocked as AssetComplianceBlocked,
    generate_character_asset,
    generate_scene_asset,
)
from app.services.bgm_synth import ensure_bgm_file, public_url, resolve_mood
from app.services.compliance import check, enforce
from app.services.jobs.queue import finish_job, update_progress
from app.services.llm.client import LLMNotConfigured
ProgressFn = Callable[[int, str], None]


@dataclass
class StepResult:
    step: str
    status: str  # completed | skipped | failed | blocked
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    merged_url: str | None = None
    drama_id: int | None = None
    episode_id: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "drama_id": self.drama_id,
            "episode_id": self.episode_id,
            "merged_url": self.merged_url,
            "error": self.error,
            "steps": [
                {
                    "step": s.step,
                    "status": s.status,
                    "message": s.message,
                    "detail": s.detail,
                }
                for s in self.steps
            ],
        }


def _progress(cb: ProgressFn | None, pct: int, msg: str) -> None:
    if cb:
        cb(max(0, min(99, pct)), msg)


def _cancelled(job: ProductionJob | None) -> bool:
    return bool(job is not None and job.cancel_requested)


def _script_text(ep: Episode) -> str:
    return (ep.script_content or ep.content or "").strip()


async def run_easy_pipeline(
    db: Session,
    *,
    episode_id: int,
    username: str | None = None,
    skip_existing: bool = True,
    do_export: bool = True,
    transition: str = "fade",
    bgm_mood: str | None = "warm",
    auto_bgm: bool = True,
    job: ProductionJob | None = None,
    progress: ProgressFn | None = None,
) -> PipelineResult:
    """对一集执行：剧本→提取→音色→角色图→场景图→分镜→分镜图→视频→配音→导出。"""
    result = PipelineResult(ok=False, episode_id=episode_id)
    ep = db.get(Episode, episode_id)
    if ep is None or getattr(ep, "deleted_at", None):
        result.error = "分集不存在"
        return result
    result.drama_id = ep.drama_id
    drama = db.get(Drama, ep.drama_id)

    def note(step: str, status: str, message: str = "", **detail: Any) -> None:
        result.steps.append(StepResult(step=step, status=status, message=message, detail=detail))

    def check_cancel() -> bool:
        if job is not None:
            db.refresh(job)
        if _cancelled(job):
            result.error = "用户取消"
            note("cancel", "failed", "已取消")
            return True
        return False

    # ── 0. 前置 ──────────────────────────────────────────
    _progress(progress, 3, "检查环境…")
    try:
        from app.services.llm.client import resolve_llm

        resolve_llm(db)
    except LLMNotConfigured as exc:
        result.error = str(exc) or "请先在「设置」配置语言模型 API"
        note("prereq_llm", "failed", result.error)
        return result
    note("prereq_llm", "completed", "语言模型已配置")

    # ── 1. 剧本 ──────────────────────────────────────────
    if check_cancel():
        return result
    _progress(progress, 8, "准备剧本…")
    if ep.script_content and ep.script_content.strip():
        note("script", "skipped", "已有剧本，跳过生成")
    elif ep.content and ep.content.strip():
        raw = check(ep.content)
        if raw.blocked:
            enforce.record_violation(db, username, raw, "easy_script_input")
            result.error = "小说原文触发红线，已拦截"
            note("script", "blocked", result.error)
            return result
        try:
            script = generate_script(db, ep.content, 0.7, episode_id=ep.id)
        except LLMNotConfigured as exc:
            result.error = str(exc)
            note("script", "failed", result.error)
            return result
        except Exception as exc:  # noqa: BLE001
            result.error = f"剧本生成失败：{exc}"
            note("script", "failed", result.error)
            return result
        out = check(script)
        if out.blocked:
            enforce.record_violation(db, username, out, "easy_script_output")
            result.error = "生成剧本触发红线"
            note("script", "blocked", result.error)
            return result
        ep.script_content = script
        db.commit()
        note("script", "completed", f"已生成剧本（约 {len(script)} 字）")
    else:
        result.error = "请先粘贴小说/大纲，或生成剧本"
        note("script", "failed", result.error)
        return result

    script = (ep.script_content or "").strip()

    # ── 2. 提取角色/场景 ─────────────────────────────────
    if check_cancel():
        return result
    _progress(progress, 15, "提取角色与场景…")
    chars = list(
        db.scalars(
            select(Character).where(
                Character.drama_id == ep.drama_id,
                Character.deleted_at.is_(None),
            )
        ).all()
    )
    scenes = list(
        db.scalars(
            select(Scene).where(Scene.drama_id == ep.drama_id, Scene.deleted_at.is_(None))
        ).all()
    )
    if skip_existing and chars and scenes:
        note("extract", "skipped", f"已有 {len(chars)} 角色 / {len(scenes)} 场景")
    else:
        try:
            extracted = extract(db, script, drama_id=ep.drama_id)
            out = check(json.dumps(extracted, ensure_ascii=False))
            if out.blocked:
                enforce.record_violation(db, username, out, "easy_extract")
                result.error = "提取结果触发红线"
                note("extract", "blocked", result.error)
                return result
            new = save_extracted(db, ep.drama_id, ep.id, extracted)
            note("extract", "completed", "角色场景已提取", new=new)
        except LLMNotConfigured as exc:
            result.error = str(exc)
            note("extract", "failed", result.error)
            return result
        except Exception as exc:  # noqa: BLE001
            # 提取失败不硬停：分镜仍可尝试
            note("extract", "failed", f"提取失败（继续）：{exc}")
            logger.warning("easy extract failed: %s", exc)

    # ── 3. 角色出图（已取消独立 TTS/音色；语音在定稿出片时由模型生成）──
    if check_cancel():
        return result
    chars = list(
        db.scalars(
            select(Character).where(
                Character.drama_id == ep.drama_id,
                Character.deleted_at.is_(None),
            )
        ).all()
    )
    char_need = [c for c in chars if not (c.image_url or c.local_path) or not skip_existing]
    if skip_existing:
        char_need = [c for c in chars if not (c.image_url or c.local_path)]
    _progress(progress, 28, f"生成角色图 0/{len(char_need) or len(chars)}…")
    char_ok = 0
    char_fail = 0
    for i, ch in enumerate(char_need):
        if check_cancel():
            return result
        _progress(progress, 28 + int(10 * i / max(len(char_need), 1)), f"角色图 {ch.name or ch.id}…")
        try:
            await generate_character_asset(db, character_id=ch.id, username=username)
            char_ok += 1
        except AssetComplianceBlocked:
            char_fail += 1
            note("character_image", "blocked", f"角色「{ch.name}」出图触发红线")
        except (AssetGenerationError, LookupError, Exception) as exc:  # noqa: BLE001
            char_fail += 1
            logger.warning("character image fail %s: %s", ch.id, exc)
    skipped_c = len(chars) - len(char_need)
    note(
        "character_images",
        "completed" if char_fail == 0 else "failed",
        f"角色图：新生成 {char_ok}，跳过 {skipped_c}，失败 {char_fail}",
        ok=char_ok,
        skipped=skipped_c,
        failed=char_fail,
    )

    # ── 5. 场景出图 ──────────────────────────────────────
    if check_cancel():
        return result
    scenes = list(
        db.scalars(
            select(Scene).where(Scene.drama_id == ep.drama_id, Scene.deleted_at.is_(None))
        ).all()
    )
    scene_need = [s for s in scenes if not (s.image_url or s.local_path)] if skip_existing else list(scenes)
    _progress(progress, 40, f"生成场景图 0/{len(scene_need) or len(scenes)}…")
    scene_ok = 0
    scene_fail = 0
    for i, sc in enumerate(scene_need):
        if check_cancel():
            return result
        _progress(progress, 40 + int(8 * i / max(len(scene_need), 1)), f"场景图 {sc.name or sc.id}…")
        try:
            await generate_scene_asset(db, scene_id=sc.id, username=username)
            scene_ok += 1
        except AssetComplianceBlocked:
            scene_fail += 1
        except (AssetGenerationError, LookupError, Exception) as exc:  # noqa: BLE001
            scene_fail += 1
            logger.warning("scene image fail %s: %s", sc.id, exc)
    note(
        "scene_images",
        "completed" if scene_fail == 0 else "failed",
        f"场景图：新生成 {scene_ok}，跳过 {len(scenes) - len(scene_need)}，失败 {scene_fail}",
        ok=scene_ok,
        failed=scene_fail,
    )

    # ── 6. 分镜拆解 ──────────────────────────────────────
    if check_cancel():
        return result
    _progress(progress, 50, "拆解分镜…")
    boards = list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == ep.id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number)
        ).all()
    )
    if skip_existing and boards:
        note("storyboard", "skipped", f"已有 {len(boards)} 个分镜")
    else:
        try:
            shots = break_storyboards(db, script, 0.65, episode_id=ep.id)
            out = check(json.dumps(shots, ensure_ascii=False))
            if out.blocked:
                enforce.record_violation(db, username, out, "easy_storyboard")
                result.error = "分镜结果触发红线"
                note("storyboard", "blocked", result.error)
                return result
            count = save_storyboards(db, ep.id, shots)
            note("storyboard", "completed", f"已拆 {count} 个分镜")
            # 拆镜后先跑一轮审核（有剧本时），问题多则整改再继续
            try:
                from app.services.storyboard_review import (
                    remediate_storyboard_review,
                    run_storyboard_review,
                )

                if (ep.script_content or "").strip():
                    rev = run_storyboard_review(
                        db,
                        ep.id,
                        instruction="拆镜刚完成，检查时长 3–5 秒、台词覆盖、空镜与人物提示词纪律。",
                    )
                    note(
                        "shot_audit_pre",
                        "completed",
                        f"拆镜后预审 {rev.get('grade')} 严重{rev.get('severe_count')}",
                        grade=rev.get("grade"),
                    )
                    if int(rev.get("severe_count") or 0) >= 1 and rev.get("id"):
                        remediate_storyboard_review(db, int(rev["id"]), None)
                        note("shot_remediate_pre", "completed", "预审后已自动整改")
            except Exception as exc:  # noqa: BLE001
                note("shot_audit_pre", "failed", f"预审跳过：{exc}")
        except LLMNotConfigured as exc:
            result.error = str(exc)
            note("storyboard", "failed", result.error)
            return result
        except Exception as exc:  # noqa: BLE001
            result.error = f"分镜拆解失败：{exc}"
            note("storyboard", "failed", result.error)
            return result

    boards = list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == ep.id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number)
        ).all()
    )
    if not boards:
        result.error = "没有可用分镜"
        note("storyboard", "failed", result.error)
        return result

    # ── 7. 分镜提示词审核（已取消分镜出图；出片用角色/场景多参考 + 台词提示词）──
    if check_cancel():
        return result
    note(
        "storyboard_images",
        "skipped",
        "已跳过分镜出图：成片走 MiniMax 多参考图 r2v，语音写在提示词中",
    )
    _progress(progress, 62, "分镜审核（提示词/台词）…")
    try:
        from app.services.storyboard_review import (
            remediate_storyboard_review,
            run_storyboard_review,
        )

        if (ep.script_content or "").strip():
            review = run_storyboard_review(
                db,
                ep.id,
                instruction=(
                    "已取消分镜静帧出图。请重点检查："
                    "video_prompt/image_prompt 是否可拍；"
                    "dialogue 是否含角色说话（名：台词），供视频模型生成语音；"
                    "空镜是否误写人物对白；时长是否 3–5 秒。"
                ),
            )
            severe = int(review.get("severe_count") or 0)
            note(
                "shot_audit",
                "completed",
                f"自动审核完成 等级 {review.get('grade')} 严重 {severe}",
                grade=review.get("grade"),
                review_id=review.get("id"),
            )
            if severe > 0 or int(review.get("medium_count") or 0) > 0:
                rid = review.get("id")
                if rid:
                    try:
                        remediate_storyboard_review(db, int(rid), None)
                        note("shot_remediate", "completed", "已按审核建议自动整改一版分镜提示词/台词")
                    except Exception as exc:  # noqa: BLE001
                        note("shot_remediate", "failed", f"自动整改跳过：{exc}")
    except Exception as exc:  # noqa: BLE001
        note("shot_audit", "failed", f"自动审核跳过：{exc}")
        logger.warning("easy shot_audit failed: %s", exc)

    # ── 8. 出视频（多参考图 r2v，不依赖分镜图）────────────────
    if check_cancel():
        return result
    from app.services.video_generation import (
        ComplianceBlocked as VideoComplianceBlocked,
        VideoGenError,
        submit_video_generation,
    )

    boards = list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == ep.id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number)
        ).all()
    )
    need_vid = [
        b
        for b in boards
        if (not (b.video_url or b.composed_video_url) if skip_existing else True)
    ]
    _progress(progress, 72, f"出视频 0/{len(need_vid)}…")
    vid_ok = vid_fail = 0

    def _poll_cancelled() -> bool:
        # C1: 供 Comfy 轮询循环调用，刷新后判断是否取消
        try:
            db.refresh(job)
        except Exception:  # noqa: BLE001
            pass
        return _cancelled(job)

    for i, sb in enumerate(need_vid):
        if check_cancel():
            return result
        _progress(progress, 72 + int(12 * i / max(len(need_vid), 1)), f"出视频 {i + 1}/{len(need_vid)}…")
        try:
            gen = await submit_video_generation(
                db,
                storyboard_id=sb.id,
                username=username,
                quality_mode="final",
                use_prev_last_frame=True,
                cancel_check=_poll_cancelled,
            )
            if gen.status == "completed" and gen.video_url:
                vid_ok += 1
            else:
                vid_fail += 1
        except VideoComplianceBlocked:
            vid_fail += 1
        except (VideoGenError, LookupError, Exception) as exc:  # noqa: BLE001
            vid_fail += 1
            logger.warning("video fail %s: %s", sb.id, exc)
    note(
        "videos",
        "completed" if vid_fail == 0 else "failed",
        f"视频：成功 {vid_ok}，跳过 {len(boards) - len(need_vid)}，失败 {vid_fail}",
        ok=vid_ok,
        failed=vid_fail,
    )

    # ── 9. 导出（语音已在定稿出片时由模型写入视频音轨）──
    note("tts", "skipped", "已取消独立配音；语音由视频模型生成")
    if not do_export:
        result.ok = True
        _progress(progress, 100, "流水线完成（未导出）")
        return result

    if check_cancel():
        return result
    _progress(progress, 92, "合成成片…")
    try:
        from app.api.timeline import (
            TimelineClip,
            TimelineDocument,
            TimelineTrack,
            TimelineTracks,
            build_timeline,
            run_timeline_export,
        )
        from app.models.domain import VideoMerge

        # 注入程序化 BGM + 转场元数据
        doc = build_timeline(db, ep.id)
        music_clips: list[TimelineClip] = []
        if auto_bgm:
            try:
                oss = settings.data_dir / "oss"
                bgm_path = ensure_bgm_file(
                    oss, duration=max(float(doc.duration or 0), 12.0), mood=resolve_mood(bgm_mood)
                )
                music_clips.append(
                    TimelineClip(
                        storyboard_id=None,
                        index=0,
                        start=0,
                        duration=max(float(doc.duration or 0), 12.0),
                        audio_url=public_url(bgm_path),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                note("bgm", "failed", f"程序化配乐跳过：{exc}")
            else:
                note("bgm", "completed", f"已混入程序化配乐（{resolve_mood(bgm_mood)}）")

        document = TimelineDocument(
            episode_id=ep.id,
            duration=doc.duration,
            tracks=TimelineTracks(
                video=doc.tracks.video,
                voiceover=doc.tracks.voiceover,
                subtitle=doc.tracks.subtitle,
                music=TimelineTrack(enabled=bool(music_clips), clips=music_clips),
            ),
        )
        trans = transition if transition in ("fade", "fadeblack", "none") else "fade"
        scenes_payload = document.model_dump(mode="json")
        scenes_payload["transition"] = trans
        scenes_payload["transition_duration"] = 0.35

        stored = db.scalars(
            select(VideoMerge)
            .where(VideoMerge.episode_id == ep.id, VideoMerge.deleted_at.is_(None))
            .order_by(VideoMerge.id.desc())
        ).first()
        if stored is None:
            stored = VideoMerge(
                episode_id=ep.id,
                drama_id=ep.drama_id,
                title=f"{ep.title or '分集'} 成片",
                provider="timeline",
            )
            db.add(stored)
        stored.scenes = json.dumps(scenes_payload, ensure_ascii=False)
        stored.status = "draft"
        stored.provider = "timeline"
        db.commit()

        export = run_timeline_export(db, ep.id, username=username)
        if export.get("blocked") or export.get("status") == "blocked":
            result.error = export.get("error") or "导出被合规拦截"
            note("export", "blocked", result.error)
            return result
        if export.get("status") != "completed":
            result.error = export.get("error") or "导出失败"
            note("export", "failed", result.error)
            return result
        result.merged_url = export.get("merged_url")
        note("export", "completed", "成片已导出", merged_url=result.merged_url)
    except Exception as exc:  # noqa: BLE001
        result.error = f"导出失败：{exc}"
        note("export", "failed", result.error)
        logger.exception("easy export failed")
        return result

    result.ok = True
    _progress(progress, 100, "一键出片完成")
    if drama is not None:
        # 轻触状态，不强制改
        pass
    return result


def bootstrap_from_idea(
    db: Session,
    *,
    title: str,
    text: str,
    username: str | None = None,
    art_style_id: int | None = None,
) -> dict[str, Any]:
    """小白入口：一句话/一段文 → 项目+第1集。"""
    text = (text or "").strip()
    title = (title or "").strip() or "我的短剧"
    if not text:
        raise ValueError("请粘贴小说、大纲或一句话剧情")
    if len(text) < 8:
        raise ValueError("内容太短，请至少写几句剧情")

    raw = check(text)
    if raw.blocked:
        en = enforce.record_violation(db, username, raw, "easy_bootstrap")
        raise PermissionError(
            json.dumps(
                {
                    "blocked": True,
                    "message": "内容触发红线，已拦截",
                    "violation_count": en.get("violation_count"),
                    "banned": en.get("banned"),
                },
                ensure_ascii=False,
            )
        )

    from app.services.style_composer import StyleConflictError, save_bible, validate_bible_dict

    drama = Drama(title=title[:120], description=text[:500], status="draft")
    bible_raw = {
        "version": 1,
        "art_style_id": art_style_id,
        "narrative_tag": None,
        "pacing_profile": "pace_balanced",
        "aspect": "9:16",
    }
    try:
        bible = validate_bible_dict(bible_raw, db)
        if bible.get("visual_name"):
            drama.style = str(bible["visual_name"])
        save_bible(drama, bible)
    except StyleConflictError:
        pass
    db.add(drama)
    db.flush()

    # 短文当剧本，长文当小说原文（后续可再改编）
    ep = Episode(
        drama_id=drama.id,
        episode_number=1,
        title="第1集",
        status="draft",
    )
    if len(text) <= 2500:
        # 小白粘贴的短剧情：直接当剧本用，少一步
        ep.script_content = text
        ep.content = text
    else:
        ep.content = text
    db.add(ep)
    db.commit()
    db.refresh(drama)
    db.refresh(ep)
    return {
        "drama_id": drama.id,
        "episode_id": ep.id,
        "title": drama.title,
        "has_script": bool(ep.script_content),
        "has_content": bool(ep.content),
    }


async def handle_easy_pipeline_job(db: Session, job: ProductionJob, payload: dict[str, Any]) -> None:
    episode_id = int(payload["episode_id"])

    def prog(pct: int, msg: str) -> None:
        update_progress(db, job, pct, msg)

    result = await run_easy_pipeline(
        db,
        episode_id=episode_id,
        username=payload.get("username") or job.username,
        skip_existing=bool(payload.get("skip_existing", True)),
        do_export=bool(payload.get("do_export", True)),
        transition=str(payload.get("transition") or "fade"),
        bgm_mood=payload.get("bgm_mood") or "warm",
        auto_bgm=bool(payload.get("auto_bgm", True)),
        job=job,
        progress=prog,
    )
    if job.cancel_requested or result.error == "用户取消":
        finish_job(db, job, status="cancelled", message="已取消", result=result.to_dict())
        return
    if result.ok:
        finish_job(
            db,
            job,
            status="completed",
            message="一键出片完成" if result.merged_url else "流水线完成",
            result=result.to_dict(),
        )
    else:
        finish_job(
            db,
            job,
            status="failed",
            error=result.error or "流水线未完成",
            result=result.to_dict(),
        )
