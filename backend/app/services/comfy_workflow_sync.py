"""ComfyUI 调试工作流导出 + 参数回同步到西瓜算力节点。

用法：
1. 导出：把角色出图 / 多参考视频 API 工作流写入 ComfyUI workflows 目录，便于在 Comfy 打开调试。
2. 同步：从 Comfy 最近 history 任务或导出的 JSON 解析 width/height/steps 等，写回 model_settings。
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import logger


T2I_WORKFLOW = "minimax-h3-t2i.api.json"
R2V_WORKFLOW = "minimax-h3-r2v.api.json"
EXPORT_T2I_NAME = "西瓜_角色场景出图_调试.json"
EXPORT_R2V_NAME = "西瓜_多参考视频_调试.json"


def _workflows_src_dir() -> Path:
    return Path(settings.workflows_dir)


def resolve_comfy_user_workflows_dir(hint: str | None = None) -> Path | None:
    """定位 ComfyUI user/default/workflows 目录。"""
    candidates: list[Path] = []
    if hint and str(hint).strip():
        candidates.append(Path(str(hint).strip()))
    env = os.environ.get("XIGUA_COMFYUI_ROOT") or os.environ.get("COMFYUI_ROOT")
    if env:
        root = Path(env)
        candidates.extend(
            [
                root / "user" / "default" / "workflows",
                root / "ComfyUI" / "user" / "default" / "workflows",
                root / "workflows",
            ]
        )
    # 常见本机路径
    for base in (
        Path(r"D:\ComfyUI\ComfyUI"),
        Path(r"D:\ComfyUI"),
        Path(r"C:\ComfyUI\ComfyUI"),
        Path.home() / "ComfyUI",
        Path.home() / "Documents" / "ComfyUI",
    ):
        candidates.append(base / "user" / "default" / "workflows")
        candidates.append(base / "ComfyUI" / "user" / "default" / "workflows")

    # 已存在的 workflows 目录优先
    for c in candidates:
        try:
            if c.is_dir():
                return c.resolve()
        except OSError:
            continue
    # 可创建：user/default 存在但 workflows 尚未建
    for c in candidates:
        try:
            parent = c.parent
            if parent.is_dir() or (parent.parent.is_dir() and parent.name == "default"):
                c.mkdir(parents=True, exist_ok=True)
                if c.is_dir():
                    return c.resolve()
        except OSError:
            continue
    return None


def _load_api_workflow(name: str) -> dict:
    path = _workflows_src_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"工作流模板不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or isinstance(value, list):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_COND_TYPES = (
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3AudioConditioningT8",
)
_STEP_TYPES = (
    "BasicScheduler",
    "MiniMaxH3DualClockSamplerT8",
    "MiniMaxH3MultiRateSamplerEXPT8",
)


def inject_image_params(wf: dict, *, width: int, height: int, steps: int, prompt: str | None = None) -> dict:
    """把参数写进 t2i API 图（ImageToVideo / AudioConditioningT8 + DualClock）。"""
    out = deepcopy(wf)
    # Turbo LoRA 工作流强制 4 步
    turbo = any(
        isinstance(n, dict)
        and n.get("class_type") in ("LoraLoaderBypassModelOnly", "LoraLoaderModelOnly", "LoraLoader")
        and ("turbo" in str((n.get("inputs") or {}).get("lora_name") or "").lower() or "4步" in str((n.get("inputs") or {}).get("lora_name") or ""))
        for n in out.values()
    )
    use_steps = 4 if turbo else int(steps)
    for node in out.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or ""
        ins = node.setdefault("inputs", {})
        if ct in _COND_TYPES:
            ins["width"] = int(width)
            ins["height"] = int(height)
            if prompt:
                ins["prompt"] = prompt
        if ct in _STEP_TYPES:
            if "steps" in ins:
                ins["steps"] = use_steps
            if "video_steps" in ins:
                ins["video_steps"] = use_steps
            if "audio_steps" in ins:
                ins["audio_steps"] = use_steps
        if ct == "SaveImage":
            ins["filename_prefix"] = "xigua_t2i_debug"
    return out


def inject_video_params(
    wf: dict,
    *,
    width: int,
    height: int,
    length: int | None = None,
    steps: int | None = None,
    prompt: str | None = None,
) -> dict:
    """把参数写进 r2v API 图。"""
    out = deepcopy(wf)
    turbo = any(
        isinstance(n, dict)
        and n.get("class_type") in ("LoraLoaderBypassModelOnly", "LoraLoaderModelOnly", "LoraLoader")
        and ("turbo" in str((n.get("inputs") or {}).get("lora_name") or "").lower() or "4步" in str((n.get("inputs") or {}).get("lora_name") or ""))
        for n in out.values()
    )
    use_steps = 4 if turbo else (int(steps) if steps is not None else None)
    for node in out.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or ""
        ins = node.setdefault("inputs", {})
        if ct in _COND_TYPES:
            ins["width"] = int(width)
            ins["height"] = int(height)
            if length is not None:
                ins["length"] = int(length)
            if prompt:
                ins["prompt"] = prompt
        if use_steps is not None and ct in _STEP_TYPES:
            if "steps" in ins:
                ins["steps"] = use_steps
            if "video_steps" in ins:
                ins["video_steps"] = use_steps
            if "audio_steps" in ins:
                ins["audio_steps"] = use_steps
        if ct in ("SaveVideo", "VHS_VideoCombine"):
            ins["filename_prefix"] = "xigua_r2v_debug"
    return out


def extract_params_from_prompt_graph(prompt: dict) -> dict[str, Any]:
    """从 Comfy history 里的 prompt 图提取可同步参数。"""
    found: dict[str, Any] = {
        "width": None,
        "height": None,
        "steps": None,
        "length": None,
        "prompt_preview": None,
        "kind": None,  # t2i | r2v | other
    }
    if not isinstance(prompt, dict):
        return found

    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or ""
        ins = node.get("inputs") or {}
        if not isinstance(ins, dict):
            continue
        if ct == "MiniMaxH3ReferenceToVideo":
            found["kind"] = "r2v"
            found["width"] = _as_int(ins.get("width"), found["width"])
            found["height"] = _as_int(ins.get("height"), found["height"])
            found["length"] = _as_int(ins.get("length"), found["length"])
            if isinstance(ins.get("prompt"), str):
                found["prompt_preview"] = ins["prompt"][:200]
        elif ct == "MiniMaxH3AudioConditioningT8":
            task = str(ins.get("task_type") or "").lower()
            has_refs = any(
                k == "ref_images" or str(k).startswith("ref_images.") for k in ins
            )
            if task in ("ref2va", "hybrid") or has_refs:
                found["kind"] = "r2v"
            elif found["kind"] is None:
                found["kind"] = "t2i" if int(ins.get("length") or 0) <= 17 else "r2v"
            found["width"] = _as_int(ins.get("width"), found["width"])
            found["height"] = _as_int(ins.get("height"), found["height"])
            found["length"] = _as_int(ins.get("length"), found["length"])
            if isinstance(ins.get("prompt"), str) and not found["prompt_preview"]:
                found["prompt_preview"] = ins["prompt"][:200]
        elif ct == "MiniMaxH3ImageToVideo":
            if found["kind"] is None:
                found["kind"] = "t2i"
            found["width"] = _as_int(ins.get("width"), found["width"])
            found["height"] = _as_int(ins.get("height"), found["height"])
            found["length"] = _as_int(ins.get("length"), found["length"])
            if isinstance(ins.get("prompt"), str) and not found["prompt_preview"]:
                found["prompt_preview"] = ins["prompt"][:200]
        if ct in _STEP_TYPES:
            found["steps"] = _as_int(ins.get("steps"), found["steps"])
            if found["steps"] is None:
                found["steps"] = _as_int(ins.get("video_steps"), found["steps"])
        if ct in ("EmptyLatentImage", "EmptySD3LatentImage", "EmptyFlux2LatentImage"):
            found["width"] = _as_int(ins.get("width"), found["width"])
            found["height"] = _as_int(ins.get("height"), found["height"])
        if ct in ("KSampler", "KSamplerAdvanced"):
            found["steps"] = _as_int(ins.get("steps"), found["steps"])
    return found


def params_to_model_settings(params: dict[str, Any], base: dict | None = None) -> dict:
    """合并到算力节点 model_settings（字符串形式与前端一致）。"""
    ms = dict(base or {})
    if params.get("width") is not None:
        ms["width"] = str(int(params["width"]))
    if params.get("height") is not None:
        ms["height"] = str(int(params["height"]))
    if params.get("steps") is not None:
        ms["steps"] = str(int(params["steps"]))
    if params.get("length") is not None:
        # 帧数 → 约秒数（24fps）存 duration 供参考
        length = int(params["length"])
        ms["frame_length"] = str(length)
        ms["duration"] = str(round(length / 24, 2))
    if params.get("kind") == "r2v":
        ms["workflow_i2v"] = R2V_WORKFLOW
        ms["workflow_video"] = R2V_WORKFLOW
    elif params.get("kind") == "t2i":
        ms["workflow_t2i"] = T2I_WORKFLOW
        ms["workflow"] = T2I_WORKFLOW
    ms["synced_from_comfy_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    return ms


def export_debug_workflows(
    *,
    model_settings: dict | None = None,
    comfy_user_dir_hint: str | None = None,
) -> dict:
    """导出两个调试工作流到 ComfyUI workflows 目录 + 西瓜 data/comfy_debug。"""
    ms = model_settings or {}
    hint = comfy_user_dir_hint or ms.get("comfy_user_dir") or ms.get("comfyui_user_dir")

    width = _as_int(ms.get("width"), 1024) or 1024
    height = _as_int(ms.get("height"), 1344) or 1344
    steps = _as_int(ms.get("steps"), 4) or 4
    # 视频默认 768x432、约 3s → 73 帧
    v_w = _as_int(ms.get("video_width"), 768) or 768
    v_h = _as_int(ms.get("video_height"), 432) or 432
    v_steps = _as_int(ms.get("video_steps"), steps) or steps
    v_len = _as_int(ms.get("frame_length"), 73) or 73

    t2i = inject_image_params(
        _load_api_workflow(T2I_WORKFLOW),
        width=width,
        height=height,
        steps=steps,
        prompt="【调试】角色定妆：超清人脸，五官锐利，电影棚拍柔光",
    )
    r2v = inject_video_params(
        _load_api_workflow(R2V_WORKFLOW),
        width=v_w,
        height=v_h,
        length=v_len,
        steps=v_steps,
        prompt="<Picture 1> 场景环境。 <Picture 2> 角色身份。【必须照念】角色说：「……」。无台词则闭嘴。no subtitles.",
    )

    # 西瓜侧备份（始终可写）
    local_dir = Path(settings.data_dir) / "comfy_debug"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_t2i = local_dir / EXPORT_T2I_NAME
    local_r2v = local_dir / EXPORT_R2V_NAME
    local_t2i.write_text(json.dumps(t2i, ensure_ascii=False, indent=2), encoding="utf-8")
    local_r2v.write_text(json.dumps(r2v, ensure_ascii=False, indent=2), encoding="utf-8")

    comfy_dir = resolve_comfy_user_workflows_dir(str(hint) if hint else None)
    written: list[str] = [str(local_t2i), str(local_r2v)]
    comfy_written: list[str] = []
    if comfy_dir is not None:
        try:
            p1 = comfy_dir / EXPORT_T2I_NAME
            p2 = comfy_dir / EXPORT_R2V_NAME
            p1.write_text(json.dumps(t2i, ensure_ascii=False, indent=2), encoding="utf-8")
            p2.write_text(json.dumps(r2v, ensure_ascii=False, indent=2), encoding="utf-8")
            comfy_written = [str(p1), str(p2)]
            written.extend(comfy_written)
        except OSError as exc:
            logger.warning("写入 Comfy workflows 失败: %s", exc)

    return {
        "ok": True,
        "comfy_workflows_dir": str(comfy_dir) if comfy_dir else None,
        "local_dir": str(local_dir),
        "files": written,
        "comfy_files": comfy_written,
        "params_exported": {
            "t2i": {"width": width, "height": height, "steps": steps},
            "r2v": {"width": v_w, "height": v_h, "steps": v_steps, "length": v_len},
        },
        "how_to_open": (
            "在 ComfyUI 菜单 Workflow → Open / Browse templates，打开 "
            f"「{EXPORT_T2I_NAME}」或「{EXPORT_R2V_NAME}」。"
            "若列表没有，用 Load 选择上述路径；API 格式请用 Dev Mode 下 Load (API format)。"
            "调参后 Queue Prompt 跑一遍，再回西瓜点「从 Comfy 同步参数」。"
        ),
    }


async def fetch_latest_history_params(
    base_url: str,
    *,
    token: str | None = None,
    max_items: int = 12,
) -> dict:
    """从 Comfy /history 取最近任务参数。"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 新版本 Comfy 支持 max_items；旧版忽略 query
        r = await client.get(f"{url}/history", params={"max_items": max_items}, headers=headers)
        r.raise_for_status()
        data = r.json()

    if not isinstance(data, dict) or not data:
        return {"ok": False, "message": "Comfy 历史为空：请先在 ComfyUI 里 Queue 跑一遍调试工作流"}

    # history 可能是 {prompt_id: entry}，按 key 倒序近似最近
    items = list(data.items())
    # 优先有 outputs 的
    def _score(item: tuple[str, Any]) -> tuple[int, str]:
        pid, entry = item
        if not isinstance(entry, dict):
            return (0, pid)
        has_out = 1 if entry.get("outputs") else 0
        return (has_out, pid)

    items.sort(key=_score, reverse=True)

    attempts: list[dict] = []
    for prompt_id, entry in items[:max_items]:
        if not isinstance(entry, dict):
            continue
        prompt = entry.get("prompt")
        # 有的版本 prompt 是 [number, number, prompt_dict, extra, outputs_to_execute]
        graph = None
        if isinstance(prompt, dict):
            graph = prompt
        elif isinstance(prompt, list) and len(prompt) >= 3 and isinstance(prompt[2], dict):
            graph = prompt[2]
        if not graph:
            continue
        params = extract_params_from_prompt_graph(graph)
        attempts.append({"prompt_id": prompt_id, "params": params})
        if params.get("width") or params.get("steps") or params.get("height"):
            return {
                "ok": True,
                "prompt_id": prompt_id,
                "params": params,
                "source": "history",
                "message": f"已从 Comfy 任务 {prompt_id[:8]}… 读取参数",
            }

    return {
        "ok": False,
        "message": "历史任务中未找到 MiniMax 尺寸/步数节点",
        "attempts": attempts[:5],
    }


def extract_from_workflow_file(path: str | Path) -> dict:
    """从导出的 API json / UI workflow 尽量提取参数。"""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict) and any(
        isinstance(v, dict) and v.get("class_type") for v in raw.values()
    ):
        # API format
        params = extract_params_from_prompt_graph(raw)
        return {"ok": True, "params": params, "source": str(p)}
    if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
        # UI format
        found: dict[str, Any] = {
            "width": None,
            "height": None,
            "steps": None,
            "length": None,
            "kind": None,
            "prompt_preview": None,
        }
        for node in raw["nodes"]:
            if not isinstance(node, dict):
                continue
            ntype = node.get("type") or ""
            widgets = node.get("widgets_values")
            if ntype == "BasicScheduler" and isinstance(widgets, list) and len(widgets) >= 2:
                found["steps"] = _as_int(widgets[1], found["steps"])
            if ntype in ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"):
                found["kind"] = "r2v" if "Reference" in ntype else "t2i"
                # widgets 顺序因版本而异，优先读 inputs 里的常量
                for inp in node.get("inputs") or []:
                    if not isinstance(inp, dict):
                        continue
                    name = inp.get("name")
                    # linked values skip
                    if name == "width" and inp.get("widget"):
                        pass
                if isinstance(widgets, list) and len(widgets) >= 4:
                    # 常见: prompt, width, height, length, ...
                    if isinstance(widgets[0], str):
                        found["prompt_preview"] = widgets[0][:200]
                    found["width"] = _as_int(widgets[1] if len(widgets) > 1 else None, found["width"])
                    found["height"] = _as_int(widgets[2] if len(widgets) > 2 else None, found["height"])
                    found["length"] = _as_int(widgets[3] if len(widgets) > 3 else None, found["length"])
        return {"ok": True, "params": found, "source": str(p)}
    return {"ok": False, "message": f"无法识别工作流格式: {p}"}
