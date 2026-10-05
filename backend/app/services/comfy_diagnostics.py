"""ComfyUI 模型/连通性检测 —— 给 4060 用户可读的「缺啥模型」报告。"""
from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.compute import get_active_node, get_node


# 仅启用 H3 Turbo 两套工作流（旧 Flux / Kontext / LTX / i2v 已废弃）
REQUIRED_HINTS = {
    "minimax-h3-t2i": {
        "label": "文生图 MiniMax H3 Turbo 4V4A",
        "files": [
            "minimax_h3_fl2va",
            "qwen3vl_32b_minimax",
            "minimax_h3_video_vae",
            "minimax_h3_audio_vae",
            "minimax_h3_turbo",
        ],
    },
    "minimax-h3-r2v": {
        "label": "多参考视频 MiniMax H3 Turbo 4V4A",
        "files": [
            "minimax_h3_fl2va",
            "qwen3vl_32b_minimax",
            "minimax_h3_video_vae",
            "minimax_h3_audio_vae",
            "minimax_h3_turbo",
        ],
    },
}


async def diagnose_comfy(db: Session, node_id: int | None = None) -> dict[str, Any]:
    try:
        node = get_node(db, node_id) if node_id else get_active_node(db)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "online": False,
            "error": str(exc),
            "message": "无可用算力节点，请先在算力页添加本地 ComfyUI。",
            "checks": [],
        }

    base = getattr(node, "base_url", None) or ""
    headers = node._headers() if hasattr(node, "_headers") else {}
    online = False
    stats: dict = {}
    models_found: set[str] = set()
    err = None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=True) as c:
            r = await c.get(f"{base.rstrip('/')}/system_stats", headers=headers)
            if r.status_code == 200:
                online = True
                stats = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            # 尝试 object_info 或 models 列表（不同 Comfy 版本接口不一）
            for path in ("/object_info", "/models/checkpoints", "/models/unet", "/models/diffusion_models"):
                try:
                    mr = await c.get(f"{base.rstrip('/')}{path}", headers=headers)
                    if mr.status_code != 200:
                        continue
                    data = mr.json()
                    _collect_names(data, models_found)
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    checks = []
    for key, meta in REQUIRED_HINTS.items():
        matched = []
        missing_hints = []
        for f in meta["files"]:
            hit = any(f.lower() in m.lower() or m.lower() in f.lower() for m in models_found)
            if hit:
                matched.append(f)
            else:
                missing_hints.append(f)
        # 若完全扫不到模型列表，标记 unknown 而非 missing
        if not models_found and online:
            status = "unknown"
            note = "已连通但无法枚举模型列表，请人工确认 ComfyUI models 目录"
        elif not online:
            status = "offline"
            note = "节点离线"
        elif not missing_hints:
            status = "ok"
            note = "疑似齐全"
        elif len(matched) == 0:
            status = "missing"
            note = "可能缺少：" + "、".join(missing_hints[:3])
        else:
            status = "partial"
            note = "部分匹配；请确认：" + "、".join(missing_hints[:3])
        checks.append(
            {
                "workflow": key,
                "label": meta["label"],
                "status": status,
                "note": note,
                "matched": matched,
                "expected": meta["files"],
            }
        )

    ok = online and not any(c["status"] == "missing" for c in checks)
    return {
        "ok": ok,
        "online": online,
        "base_url": base,
        "error": err,
        "stats": stats,
        "model_count_seen": len(models_found),
        "checks": checks,
        "gpu_hint": "RTX 4060 8G：定稿 LTX 请降分辨率；全局任务队列同时只跑 1 个 Comfy 任务。",
        "message": "连通正常" if online else (err or "连接失败"),
    }


def _collect_names(data: Any, out: set[str]) -> None:
    if isinstance(data, list):
        for x in data:
            if isinstance(x, str):
                out.add(x)
            else:
                _collect_names(x, out)
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str) and ("." in k or len(k) > 4):
                out.add(k)
            _collect_names(v, out)
