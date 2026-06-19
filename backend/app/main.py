"""FastAPI 入口 —— 西瓜短剧Agent（国内版）后端引擎。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.compliance import router as compliance_router
from app.api.compute import router as compute_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.db import init_db
from app.core.logging import logger, setup_logging
from app.services.compliance import dictionary


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    dictionary.load()
    logger.info("启动完成 | 词库 %s", dictionary.stats())
    yield


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(compliance_router)
app.include_router(compute_router)


@app.get("/")
def root() -> dict:
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
