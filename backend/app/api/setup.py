"""首次运行 / 环境向导：检测 Python 依赖外的运行条件。"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.services.comfy_diagnostics import diagnose_comfy
from app.services.llm.client import LLMNotConfigured, resolve_llm

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/public-config")
def public_config() -> dict:
    """前端启动时拉取：授权服务器公网/本机地址（不含密钥）。

    安装包 config.env 里配置 XIGUA_AUTH_SERVER_URL 后，用户打开短剧软件会连这里返回的地址。
    """
    return {
        "auth_server_url": settings.auth_server_url.rstrip("/"),
        "license_enforce": bool(settings.license_enforce),
        "app_name": settings.app_name,
        "version": settings.version,
    }


@router.get("/wizard")
async def setup_wizard(db: Session = Depends(get_db)) -> dict:
    """返回首次运行检查清单（不阻断启动）。"""
    steps: list[dict] = []

    # 1. 数据目录
    data_ok = settings.data_dir.exists()
    oss_ok = (settings.data_dir / "oss").exists()
    steps.append(
        {
            "id": "data_dir",
            "title": "数据目录",
            "ok": data_ok and oss_ok,
            "detail": str(settings.data_dir),
            "fix": None if data_ok else "自动创建失败时请手动建立 backend/data/oss",
        }
    )

    # 2. ffmpeg
    ff = shutil.which("ffmpeg")
    steps.append(
        {
            "id": "ffmpeg",
            "title": "ffmpeg（成片导出）",
            "ok": bool(ff),
            "detail": ff or "未在 PATH 中找到",
            "fix": None if ff else "新手：安装 ffmpeg 并勾选「加入 PATH」，否则最后拼不成片",
        }
    )

    # 3. LLM
    llm_ok = False
    llm_detail = "未配置"
    try:
        base, _, model = resolve_llm(db)
        llm_ok = True
        llm_detail = f"{model} @ {base}"
    except LLMNotConfigured:
        pass
    steps.append(
        {
            "id": "llm",
            "title": "语言模型",
            "ok": llm_ok,
            "detail": llm_detail,
            "fix": None
            if llm_ok
            else "新手：设置里选 DeepSeek/通义，填 API Key 并测试（只用于写剧本和分镜提示词，不画图）",
        }
    )

    # 4. 出图/出片：本机 ComfyUI（产品主路径）
    diag = await diagnose_comfy(db)
    comfy_online = bool(diag.get("online"))
    steps.append(
        {
            "id": "comfy",
            "title": "ComfyUI 出图/出片",
            "ok": comfy_online,
            "detail": str(diag.get("base_url") or "") + (" · 在线" if comfy_online else " · 离线"),
            "fix": None
            if comfy_online
            else "新手：启动本机 ComfyUI（默认 8188），再到「算力」确认绿灯。画面不走云 API。",
            "checks": diag.get("checks") or [],
            "gpu_hint": diag.get("gpu_hint"),
        }
    )

    # 5. Comfy 工作流文件
    wf_dir = Path(__file__).resolve().parent.parent / "workflows"
    needed = [
        "minimax-h3-t2i.api.json",
        "minimax-h3-r2v.api.json",
    ]
    missing = [n for n in needed if not (wf_dir / n).is_file()]
    steps.append(
        {
            "id": "workflows",
            "title": "Comfy 工作流文件",
            "ok": not missing,
            "detail": f"目录 {wf_dir}" if not missing else f"缺少: {', '.join(missing)}",
            "fix": None if not missing else "从仓库 backend/app/workflows 补齐 JSON",
        }
    )

    # 6. 推荐档位
    steps.append(
        {
            "id": "quality_modes",
            "title": "出片档位",
            "ok": True,
            "detail": "定稿=Comfy MiniMax H3 图生视频 · 建议 3–5s/镜",
            "fix": "4060 8G：任务队列同时只跑 1 个 Comfy 任务",
        }
    )

    ready = all(s["ok"] for s in steps if s["id"] in ("data_dir", "workflows"))
    production_ready = ready and all(s["ok"] for s in steps if s["id"] in ("ffmpeg", "comfy", "llm"))
    return {
        "app": settings.app_name,
        "version": settings.version,
        "ready": ready,
        "production_ready": production_ready,
        "steps": steps,
        "next_actions": [
            "设置 → 配置语言模型 API（只写剧本/提示词）并测试",
            "启动本机 ComfyUI :8188 → 算力页确认在线",
            "一键出片 → 粘贴剧情开干",
        ],
        "stack": {
            "text": "llm_api",
            "image_video": "comfyui",
        },
    }
