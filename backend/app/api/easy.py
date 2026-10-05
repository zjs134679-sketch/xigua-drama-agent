"""小白简易模式 API：从想法到成片。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.user_state import ensure_active_user
from app.core.db import get_db
from app.models.domain import Episode
from app.services.bgm_synth import list_moods
from app.services.easy_pipeline import bootstrap_from_idea, run_easy_pipeline
from app.services.jobs import enqueue_job, job_view
from app.services.license_gate import require_valid_license

router = APIRouter(prefix="/easy", tags=["easy"])


class BootstrapBody(BaseModel):
    title: str = Field(default="我的短剧", max_length=120)
    text: str = Field(..., min_length=8, description="小说/大纲/一句话剧情")
    art_style_id: int | None = None
    username: str | None = None


class PipelineBody(BaseModel):
    episode_id: int
    username: str | None = None
    skip_existing: bool = True
    do_export: bool = True
    transition: str = "fade"  # fade | fadeblack | none
    bgm_mood: str = "warm"
    auto_bgm: bool = True
    async_mode: bool = True


@router.get("/moods")
def easy_moods() -> dict:
    return {"moods": list_moods()}


@router.get("/checklist")
async def easy_checklist(db: Session = Depends(get_db)) -> dict:
    """小白开片前检查。

    产品路径：
    - LLM API：只负责剧本 / 分镜 / 提示词等文字
    - ComfyUI：负责出图、出视频（本机或远程）
    """
    from app.api.setup import setup_wizard

    data = await setup_wizard(db)
    tips: list[str] = []
    llm_ok = False
    ffmpeg_ok = False
    comfy_ok = False
    workflows_ok = False
    for s in data.get("steps") or []:
        sid = s.get("id")
        ok = bool(s.get("ok"))
        if sid == "llm":
            llm_ok = ok
        elif sid == "ffmpeg":
            ffmpeg_ok = ok
        elif sid == "comfy":
            comfy_ok = ok
        elif sid == "workflows":
            workflows_ok = ok
        if ok:
            continue
        if sid == "llm":
            tips.append(
                "还没配置「文案 AI」：打开「设置」，选 DeepSeek/通义，填 API Key 并点测试。"
                "（只用于写剧本和提示词，不负责画图）"
            )
        elif sid == "comfy":
            tips.append(
                "还没连上 ComfyUI：请先启动本机 ComfyUI（默认端口 8188），"
                "再到「算力」页确认在线。出图、出视频都走 Comfy，不是云 API。"
            )
        elif sid == "ffmpeg":
            tips.append("还没装 ffmpeg：安装后才能把各镜头拼成完整短剧 MP4。")
        elif sid == "workflows":
            tips.append("Comfy 工作流文件不齐：请检查安装包里的 workflows 目录是否完整。")

    ready = bool(llm_ok and comfy_ok)
    if ready and not ffmpeg_ok:
        tips.append("提示：未检测到 ffmpeg，前面能生成镜头，但最后「导出成片」可能失败。")
    if ready and not workflows_ok:
        tips.append("提示：工作流文件可能不全，出图/出片前请到「算力」做一次模型检测。")

    if ready:
        headline = "环境就绪，可以一键出片了"
        summary = "文案走 API，画面走本机 ComfyUI。粘贴剧情后点「一键出片」即可。"
    else:
        headline = "再补两步就能开始"
        summary = "① 设置里配置语言模型 API（剧本/提示词）② 启动 ComfyUI 并确保算力在线（出图/出片）。"

    return {
        "ready": ready,
        "version": data.get("version"),
        "tips": tips,
        "steps": data.get("steps") or [],
        "headline": headline,
        "summary": summary,
        "paths": {
            "llm": llm_ok,
            "comfy": comfy_ok,
            "ffmpeg": ffmpeg_ok,
            "workflows": workflows_ok,
        },
        "stack": {
            "text": "llm_api",
            "image_video": "comfyui",
        },
    }


@router.post("/bootstrap")
def easy_bootstrap(
    body: BootstrapBody,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
    ensure_active_user(db, body.username)
    try:
        return bootstrap_from_idea(
            db,
            title=body.title,
            text=body.text,
            username=body.username,
            art_style_id=body.art_style_id,
        )
    except PermissionError as exc:
        try:
            detail = json.loads(str(exc))
        except json.JSONDecodeError:
            detail = {"blocked": True, "message": str(exc)}
        raise HTTPException(status_code=451, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/pipeline")
async def easy_pipeline(
    body: PipelineBody,
    db: Session = Depends(get_db),
    _license: dict = Depends(require_valid_license),
) -> dict:
    ensure_active_user(db, body.username)
    ep = db.get(Episode, body.episode_id)
    if ep is None:
        raise HTTPException(404, "分集不存在")

    if body.async_mode:
        job = enqueue_job(
            db,
            job_type="easy_pipeline",
            payload={
                "episode_id": body.episode_id,
                "username": body.username,
                "skip_existing": body.skip_existing,
                "do_export": body.do_export,
                "transition": body.transition,
                "bgm_mood": body.bgm_mood,
                "auto_bgm": body.auto_bgm,
            },
            drama_id=ep.drama_id,
            episode_id=ep.id,
            username=body.username,
            message="一键出片流水线",
        )
        return {"async": True, "status": "pending", "job": job_view(job)}

    result = await run_easy_pipeline(
        db,
        episode_id=body.episode_id,
        username=body.username,
        skip_existing=body.skip_existing,
        do_export=body.do_export,
        transition=body.transition,
        bgm_mood=body.bgm_mood,
        auto_bgm=body.auto_bgm,
    )
    status = 200 if result.ok else 502
    if any(s.status == "blocked" for s in result.steps):
        status = 451
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content=result.to_dict())
