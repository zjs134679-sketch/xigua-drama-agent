"""FastAPI 入口 —— 西瓜短剧Agent（国内版）后端引擎。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.api.agent import router as agent_router
from app.api.compliance import router as compliance_router
from app.api.assets import assets_router, styles_router
from app.api.compute import router as compute_router
from app.api.easy import router as easy_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.extract import router as extract_router
from app.api.jobs import router as jobs_router
from app.api.project_manuals import router as project_manuals_router
from app.api.projects import router as projects_router
from app.api.script import router as script_router
from app.api.settings import router as settings_router
from app.api.setup import router as setup_router
from app.api.skills import router as skills_router
from app.api.storyboard import router as storyboard_router
from app.api.storyboard_review import router as storyboard_review_router
from app.api.timeline import router as timeline_router
from app.api.vendors import router as vendors_router
from app.api.video import router as video_router
from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.logging import logger, setup_logging
from app.models.domain import ProductionJob
from app.services.compliance import dictionary
from app.services.compliance.sync import sync_dictionary
from app.services.jobs import start_worker, stop_worker

from sqlalchemy import select
from datetime import datetime


def _reset_stale_jobs() -> None:
    """C4: 启动时把上次崩溃/重启遗留的 running 任务标记为失败，避免前端永久显示"执行中"。"""
    try:
        with SessionLocal() as db:
            stale = db.scalars(select(ProductionJob).where(ProductionJob.status == "running")).all()
            for job in stale:
                job.status = "failed"
                job.error_msg = "服务重启中断"
                job.message = "服务重启中断"
                job.progress = 100
                job.completed_at = datetime.utcnow()
            db.commit()
        if stale:
            logger.info("重置遗留 running 任务 %d 个", len(stale))
    except Exception as exc:  # noqa: BLE001
        logger.warning("重置遗留任务失败: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    dictionary.load()
    sync_dictionary()
    _reset_stale_jobs()
    start_worker()
    logger.info("启动完成 | 词库 %s | 任务队列 worker 已开", dictionary.stats())
    yield
    await stop_worker()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)


class _ApiPrefixMiddleware(BaseHTTPMiddleware):
    """生产包同源访问：前端请求 /api/* → 后端路由 /*（与 Vite 开发代理一致）。"""

    async def dispatch(self, request: Request, call_next):
        path = request.scope.get("path", "")
        if path == "/api" or path.startswith("/api/"):
            request.scope["path"] = path[4:] or "/"
        return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(_ApiPrefixMiddleware)

app.include_router(agent_router)
app.include_router(health_router)
app.include_router(compliance_router)
app.include_router(compute_router)
app.include_router(projects_router)
app.include_router(project_manuals_router)
app.include_router(script_router)
app.include_router(settings_router)
app.include_router(setup_router)
app.include_router(storyboard_router)
app.include_router(storyboard_review_router)
app.include_router(skills_router)
app.include_router(extract_router)
app.include_router(styles_router)
app.include_router(assets_router)
app.include_router(timeline_router)
app.include_router(vendors_router)
app.include_router(video_router)
app.include_router(jobs_router)
app.include_router(events_router)
app.include_router(easy_router)

oss_dir = settings.data_dir / "oss"
oss_dir.mkdir(parents=True, exist_ok=True)
app.mount("/oss", StaticFiles(directory=oss_dir), name="oss")


@app.get("/meta")
def root_meta() -> dict:
    return {"app": settings.app_name, "version": settings.version, "docs": "/docs"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_json({"echo": msg})
    except WebSocketDisconnect:
        pass


def _resolve_frontend_dist() -> Path:
    import os
    import sys

    candidates: list[Path] = []
    env = os.environ.get("XIGUA_FRONTEND_DIST")
    if env:
        candidates.append(Path(env))
    # 始终尝试 exe 同级 web（Nuitka onefile 下 sys.frozen/__compiled__ 不一定可见）
    try:
        candidates.append(Path(sys.executable).resolve().parent / "web")
    except Exception:
        pass
    # 开发：backend/web 或仓库 frontend/dist
    candidates.append(Path(__file__).resolve().parents[2] / "web")
    candidates.append(Path(__file__).resolve().parents[2].parent / "frontend" / "dist")
    for c in candidates:
        try:
            if (c / "index.html").is_file():
                return c
        except OSError:
            continue
    return candidates[0]


_frontend_dist = _resolve_frontend_dist()
if (_frontend_dist / "index.html").is_file():
    _assets = _frontend_dist / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="frontend_assets")
    logger.info("托管前端静态资源 | %s", _frontend_dist)

    @app.get("/")
    def spa_index():
        return FileResponse(_frontend_dist / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        reserved = (
            "docs",
            "openapi.json",
            "openapi",
            "redoc",
            "oss",
            "meta",
            "ws",
            "api",
            "health",
            "projects",
            "assets",
            "jobs",
            "skills",
            "settings",
            "video",
            "events",
            "script",
            "extract",
            "storyboard",
            "compute",
            "compliance",
            "setup",
            "agent",
            "timeline",
            "vendors",
            "art-styles",
            "easy",
        )
        first = (full_path or "").split("/", 1)[0]
        if first in reserved or full_path.startswith("assets/"):
            from fastapi import HTTPException

            raise HTTPException(404)
        candidate = _frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_frontend_dist / "index.html")
else:

    @app.get("/")
    def root_api_only() -> dict:
        return {"app": settings.app_name, "version": settings.version, "docs": "/docs"}

    logger.info("未找到前端 dist，仅 API 模式")
