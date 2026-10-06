"""任务处理器：视频生成 / 批量 / 事件提取 / 导出。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain import ProductionJob
from app.services.jobs.queue import finish_job, update_progress


def _job_cancelled(db: Session, job: ProductionJob) -> bool:
    """供 Comfy 轮询循环调用的取消检查：刷新后判断 cancel_requested。"""
    try:
        db.refresh(job)
    except Exception:  # noqa: BLE001
        pass
    return bool(job.cancel_requested)


async def handle_job(db: Session, job: ProductionJob, payload: dict[str, Any]) -> None:
    jtype = job.job_type
    if jtype == "video_generate":
        await _video_generate(db, job, payload)
    elif jtype == "video_batch":
        await _video_batch(db, job, payload)
    elif jtype == "extract_novel_events":
        await _extract_events(db, job, payload)
    elif jtype == "adapt_events":
        await _adapt_events(db, job, payload)
    elif jtype == "export_timeline":
        await _export_timeline(db, job, payload)
    elif jtype == "production_supervise":
        await _supervise(db, job, payload)
    elif jtype == "easy_pipeline":
        from app.services.easy_pipeline import handle_easy_pipeline_job

        await handle_easy_pipeline_job(db, job, payload)
    else:
        finish_job(db, job, status="failed", error=f"未知任务类型: {jtype}")


async def _video_generate(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.services.video_generation import ComplianceBlocked, VideoGenError, submit_video_generation

    if job.cancel_requested:
        finish_job(db, job, status="cancelled", message="已取消")
        return
    update_progress(db, job, 15, "提交 ComfyUI 视频任务…")
    try:
        gen = await submit_video_generation(
            db,
            storyboard_id=int(payload["storyboard_id"]),
            prompt=payload.get("prompt"),
            model=payload.get("model") or "default",
            reference_mode=payload.get("reference_mode") or "single",
            node_id=payload.get("node_id"),
            username=job.username or payload.get("username"),
            duration=int(payload.get("duration") or 5),
            resolution=payload.get("resolution"),
            extra=payload.get("extra"),
            use_prev_last_frame=bool(payload.get("use_prev_last_frame", True)),
            quality_mode=payload.get("quality_mode") or "final",
            # C1: Comfy 轮询中定期检查取消，取消时 POST /interrupt 中断远端任务
            cancel_check=lambda: _job_cancelled(db, job),
        )
        # 可中断点：提交返回后再次确认，避免取消与完成竞态
        db.refresh(job)
        if job.cancel_requested:
            finish_job(db, job, status="cancelled", message="已取消")
            return
        if gen.status != "completed":
            finish_job(
                db,
                job,
                status="failed",
                error=gen.error_msg or "视频生成失败",
                result={"video_id": gen.id, "status": gen.status},
            )
            return
        finish_job(
            db,
            job,
            status="completed",
            message="视频已生成",
            result={
                "video_id": gen.id,
                "video_url": gen.video_url,
                "storyboard_id": gen.storyboard_id,
                "model": gen.model,
                "quality_mode": getattr(gen, "quality_mode", payload.get("quality_mode")),
            },
        )
    except ComplianceBlocked as exc:
        finish_job(db, job, status="failed", error="内容触发红线", result={"blocked": True})
    except (LookupError, VideoGenError) as exc:
        finish_job(db, job, status="failed", error=str(exc))


async def _video_batch(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.services.video_generation import submit_batch_video_generation

    ids = list(payload.get("storyboard_ids") or [])
    if not ids:
        finish_job(db, job, status="failed", error="未指定分镜")
        return
    update_progress(db, job, 10, f"批量出视频 0/{len(ids)}")
    # 逐个提交以便进度与取消
    from app.services.video_generation import ComplianceBlocked, VideoGenError, submit_video_generation

    results: list[dict] = []
    for i, sb_id in enumerate(ids):
        db.refresh(job)
        if job.cancel_requested:
            finish_job(
                db,
                job,
                status="cancelled",
                message=f"已取消（完成 {len(results)}/{len(ids)}）",
                result={"results": results},
            )
            return
        pct = 10 + int(80 * i / max(len(ids), 1))
        update_progress(db, job, pct, f"批量出视频 {i + 1}/{len(ids)}")
        try:
            gen = await submit_video_generation(
                db,
                storyboard_id=int(sb_id),
                model=payload.get("model") or "default",
                reference_mode=payload.get("reference_mode") or "single",
                node_id=payload.get("node_id"),
                username=job.username or payload.get("username"),
                duration=int(payload.get("duration") or 5),
                resolution=payload.get("resolution"),
                use_prev_last_frame=bool(payload.get("use_prev_last_frame", True)),
                quality_mode=payload.get("quality_mode") or "final",
                # C1: Comfy 轮询中定期检查取消，取消时 POST /interrupt 中断远端任务
                cancel_check=lambda: _job_cancelled(db, job),
            )
            results.append(
                {
                    "storyboard_id": sb_id,
                    "status": gen.status,
                    "video_url": gen.video_url,
                    "video_id": gen.id,
                    "error": gen.error_msg,
                }
            )
        except ComplianceBlocked:
            results.append({"storyboard_id": sb_id, "status": "blocked"})
        except (LookupError, VideoGenError) as exc:
            results.append({"storyboard_id": sb_id, "status": "failed", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            results.append({"storyboard_id": sb_id, "status": "failed", "error": str(exc)})
    ok = sum(1 for r in results if r.get("status") == "completed" or r.get("video_url"))
    finish_job(
        db,
        job,
        status="completed",
        message=f"批量完成：成功 {ok}/{len(ids)}",
        result={"results": results},
    )


async def _extract_events(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.services.novel_events import extract_events_from_text

    update_progress(db, job, 20, "提取章节事件…")
    drama_id = int(payload["drama_id"])
    text = payload.get("text") or ""
    chapter_title = payload.get("chapter_title")
    result = await extract_events_from_text(
        db,
        drama_id=drama_id,
        text=text,
        chapter_title=chapter_title,
        chapter_number=int(payload.get("chapter_number") or 1),
    )
    finish_job(db, job, status="completed", message=f"已提取 {result['event_count']} 个事件", result=result)


async def _adapt_events(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.services.novel_events import adapt_events_to_episode

    update_progress(db, job, 25, "按事件改编剧本…")
    result = await adapt_events_to_episode(
        db,
        drama_id=int(payload["drama_id"]),
        event_ids=list(payload.get("event_ids") or []),
        episode_id=payload.get("episode_id"),
        episode_title=payload.get("episode_title"),
    )
    finish_job(db, job, status="completed", message="事件改编完成", result=result)


async def _export_timeline(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.api.timeline import run_timeline_export

    update_progress(db, job, 20, "合成成片（含 trim 裁切）…")
    episode_id = int(payload["episode_id"])
    try:
        result = run_timeline_export(
            db,
            episode_id,
            username=job.username or payload.get("username"),
        )
        if result.get("blocked") or result.get("status") == "blocked":
            finish_job(db, job, status="failed", error=result.get("error") or "字幕红线拦截", result=result)
            return
        if result.get("status") != "completed":
            finish_job(db, job, status="failed", error=result.get("error") or "导出失败", result=result)
            return
        finish_job(
            db,
            job,
            status="completed",
            message="成片已导出",
            result={
                "merged_url": result.get("merged_url"),
                "duration": result.get("duration"),
                "episode_id": episode_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        finish_job(db, job, status="failed", error=str(exc))


async def _supervise(db: Session, job: ProductionJob, payload: dict) -> None:
    from app.services.production_supervise import supervise_episode

    update_progress(db, job, 30, "生产监督审查…")
    report = await supervise_episode(db, episode_id=int(payload["episode_id"]))
    finish_job(db, job, status="completed", message=report.get("summary", "审查完成"), result=report)
