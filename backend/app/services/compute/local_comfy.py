"""本地 ComfyUI 适配器 —— 移植 xigua-drama 已验证的调用序列：
载入 API 工作流 → 注入 positive/seed/尺寸 → POST /prompt → 轮询 /history → /view 下载。
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import random
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.compute.base import ComputeNode, ImageJob, JobResult
from app.services.compute.reference_guard import host_is_public


class LocalComfyNode(ComputeNode):
    type = "local_comfy"

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        model_settings: dict | None = None,
    ) -> None:
        super().__init__(base_url or settings.comfyui_base_url, token)
        self.model_settings = model_settings or {}

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.base_url}/system_stats", headers=self._headers())
                return r.status_code == 200
        except Exception:
            return False

    async def interrupt(self) -> bool:
        """POST ComfyUI /interrupt，尽力中断远端正在执行的任务（任务取消时调用）。"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(f"{self.base_url}/interrupt", headers=self._headers())
                return r.status_code < 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("ComfyUI /interrupt 失败: %s", exc)
            return False

    # 已废弃工作流名 → 当前唯一启用的 Turbo 模板
    _LEGACY_WORKFLOW_MAP = {
        "flux-t2i.api.json": "minimax-h3-t2i.api.json",
        "flux-t2i-gguf.legacy.api.json": "minimax-h3-t2i.api.json",
        "flux2-klein-triref.api.json": "minimax-h3-t2i.api.json",
        "kontext-multiref.api.json": "minimax-h3-t2i.api.json",
        "minimax-h3-i2v.api.json": "minimax-h3-r2v.api.json",
        "ltx23-i2v.api.json": "minimax-h3-r2v.api.json",
        "vid2vid.api.json": "minimax-h3-r2v.api.json",
    }

    @classmethod
    def _canonical_workflow(cls, name: str | None, *, kind: str = "image") -> str:
        """旧工作流名一律映射到唯一启用的两套：t2i / r2v。"""
        raw = (name or "").strip()
        mapped = cls._LEGACY_WORKFLOW_MAP.get(raw, raw) if raw else ""
        if raw and mapped != raw:
            logger.info("废弃工作流 %s → %s", raw, mapped)
        if kind == "video":
            return "minimax-h3-r2v.api.json"
        return "minimax-h3-t2i.api.json"

    def _load_workflow(self, name: str) -> dict:
        path: Path = settings.workflows_dir / name
        if not path.is_file():
            # 兼容仍指向已归档文件名的配置
            alt = self._LEGACY_WORKFLOW_MAP.get(name)
            if alt:
                path = settings.workflows_dir / alt
                logger.warning("工作流 %s 已废弃，改用 %s", name, alt)
        return json.loads(path.read_text(encoding="utf-8-sig"))

    # MiniMax H3 条件节点（官方 ReferenceToVideo / ImageToVideo + T8 AudioConditioning）
    _MINIMAX_COND_TYPES = (
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3ReferenceToVideo",
        "MiniMaxH3AudioConditioningT8",
    )
    _MINIMAX_R2V_TYPES = (
        "MiniMaxH3ReferenceToVideo",
        "MiniMaxH3AudioConditioningT8",
    )
    _MINIMAX_STEP_TYPES = (
        "BasicScheduler",
        "MiniMaxH3DualClockSamplerT8",
        "MiniMaxH3MultiRateSamplerEXPT8",
    )

    @staticmethod
    def _has_turbo_lora(wf: dict) -> bool:
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") not in (
                "LoraLoaderBypassModelOnly",
                "LoraLoaderModelOnly",
                "LoraLoader",
            ):
                continue
            name = str((node.get("inputs") or {}).get("lora_name") or "").lower()
            if "turbo" in name or "4步" in name or "4step" in name:
                return True
        return False

    @staticmethod
    def _non_pruned_unet_name(name: str) -> str:
        """Turbo LoRA 需要 AdaLN 2688；pruned 基模 AdaLN=8，运行时会 mat1×mat2 崩溃。"""
        if not name or "pruned" not in name.lower():
            return name
        # minimax_h3_fl2va_pruned_int8_convrot → minimax_h3_fl2va_int8_convrot
        fixed = name.replace("_pruned_", "_").replace("pruned_", "").replace("_pruned", "")
        return fixed

    @staticmethod
    def _ensure_turbo_compatible_base(wf: dict) -> None:
        """挂载 Turbo 4 步 LoRA 时强制使用非 pruned 基模。

        官方说明：pruned 把 AdaLN 压成 8 维，Turbo LoRA 仍按 2688 维训练；
        Bypass 加载后会在 SamplerCustomAdvanced 报
        mat1 and mat2 shapes cannot be multiplied (1x8 and 2688x16)。
        """
        if not LocalComfyNode._has_turbo_lora(wf):
            return
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") not in ("UNETLoader", "UNETLoaderGGUF", "CheckpointLoaderSimple"):
                continue
            ins = node.setdefault("inputs", {})
            for key in ("unet_name", "ckpt_name", "model_name"):
                raw = ins.get(key)
                if not isinstance(raw, str) or not raw:
                    continue
                fixed = LocalComfyNode._non_pruned_unet_name(raw)
                if fixed != raw:
                    logger.warning(
                        "Turbo LoRA 不兼容 pruned 基模，已自动替换 %s → %s",
                        raw,
                        fixed,
                    )
                    ins[key] = fixed

    @staticmethod
    def _normalize_minimax_r2v_ref_keys(wf: dict) -> None:
        """ComfyUI V3 Autogrow 只认 ref_images.ref_image_N，不能写 ref_image_N。"""
        for node in wf.values():
            if not isinstance(node, dict) or node.get("class_type") not in LocalComfyNode._MINIMAX_R2V_TYPES:
                continue
            ins = node.setdefault("inputs", {})
            # 扁平键 ref_image_0 → ref_images.ref_image_0
            for key in list(ins.keys()):
                if key.startswith("ref_image_") and key[len("ref_image_") :].isdigit():
                    ins[f"ref_images.{key}"] = ins.pop(key)
            # 若只有错键被清掉后无参考，保留已有正确键
            fixed = [k for k in ins if k.startswith("ref_images.ref_image_")]
            logger.info("r2v normalize ref keys (%s) -> %s", node.get("class_type"), fixed)

    @staticmethod
    def _resize_reference_chain(wf: dict, count: int) -> None:
        """让 Kontext 参考链与实际参考图数量一致，支持场景 + 多个人物。"""
        if count < 1:
            return
        method = next(
            (node for node in wf.values() if node.get("class_type") == "FluxKontextMultiReferenceLatentMethod"),
            None,
        )
        if method is None:
            return
        ref = method.get("inputs", {}).get("conditioning")
        chain: list[tuple[str, str, str]] = []
        while isinstance(ref, list) and ref and ref[0] in wf:
            ref_id = str(ref[0])
            ref_node = wf[ref_id]
            if ref_node.get("class_type") != "ReferenceLatent":
                break
            latent_ref = ref_node.get("inputs", {}).get("latent")
            if not isinstance(latent_ref, list) or not latent_ref or latent_ref[0] not in wf:
                break
            encode_id = str(latent_ref[0])
            pixels_ref = wf[encode_id].get("inputs", {}).get("pixels")
            if not isinstance(pixels_ref, list) or not pixels_ref or pixels_ref[0] not in wf:
                break
            load_id = str(pixels_ref[0])
            chain.append((ref_id, encode_id, load_id))
            ref = ref_node.get("inputs", {}).get("conditioning")
        chain.reverse()
        if not chain:
            return

        if count < len(chain):
            for ref_id, encode_id, load_id in chain[count:]:
                wf.pop(ref_id, None)
                wf.pop(encode_id, None)
                wf.pop(load_id, None)
            method["inputs"]["conditioning"] = [chain[count - 1][0], 0]
            return
        if count == len(chain):
            return

        numeric_ids = [int(node_id) for node_id in wf if str(node_id).isdigit()]
        next_id = max(numeric_ids, default=0) + 1
        current_conditioning = [chain[-1][0], 0]
        vae_ref = wf[chain[0][1]]["inputs"]["vae"]
        for reference_index in range(len(chain) + 1, count + 1):
            load_id, encode_id, ref_id = str(next_id), str(next_id + 1), str(next_id + 2)
            next_id += 3
            wf[load_id] = {
                "class_type": "LoadImage",
                "_meta": {"title": f"Reference {reference_index}"},
                "inputs": {"image": f"reference-{reference_index}.png", "upload": "image"},
            }
            wf[encode_id] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": [load_id, 0], "vae": vae_ref},
            }
            wf[ref_id] = {
                "class_type": "ReferenceLatent",
                "inputs": {"conditioning": current_conditioning, "latent": [encode_id, 0]},
            }
            current_conditioning = [ref_id, 0]
        method["inputs"]["conditioning"] = current_conditioning

    @staticmethod
    def _setting_int(model_settings: dict, key: str, fallback: int | None) -> int | None:
        value = model_settings.get(key)
        if value in (None, ""):
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _setting_float(model_settings: dict, key: str, fallback: float | None) -> float | None:
        value = model_settings.get(key)
        if value in (None, ""):
            return fallback
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _setting_str(model_settings: dict, *keys: str) -> str | None:
        for key in keys:
            value = model_settings.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _workflow_for_job(job: ImageJob, model_settings: dict) -> str:
        """让算力节点真正决定工作流。

        西瓜 Agent 上层会按默认 FLUX/Kontext 传 workflow；但用户在节点里配置
        FLUX2 Klein 时，应由节点自己的 workflow_t2i / workflow_refs 覆盖。
        """
        if job.reference_images:
            override = LocalComfyNode._setting_str(
                model_settings,
                "workflow_refs",
                "workflow_multiref",
                "reference_workflow",
                "workflow",
            )
        else:
            override = LocalComfyNode._setting_str(
                model_settings,
                "workflow_t2i",
                "text_workflow",
                "workflow",
            )
        return LocalComfyNode._canonical_workflow(override or job.workflow, kind="image")

    @staticmethod
    def _apply_model_settings(wf: dict, model_settings: dict) -> None:
        """把模型设置映射到 ComfyUI API 工作流输入。

        不绑定固定节点 ID：工作流里只要出现相同输入字段，就会覆盖。
        """
        value_by_input = {
            "ckpt_name": LocalComfyNode._setting_str(model_settings, "ckpt_name", "checkpoint", "model"),
            "unet_name": LocalComfyNode._setting_str(model_settings, "unet_name", "unet", "model"),
            "model_name": LocalComfyNode._setting_str(model_settings, "model_name", "model"),
            "clip_name": LocalComfyNode._setting_str(model_settings, "clip_name", "clip"),
            "clip_name1": LocalComfyNode._setting_str(model_settings, "clip_name1", "clip1"),
            "clip_name2": LocalComfyNode._setting_str(model_settings, "clip_name2", "clip2"),
            "clip1": LocalComfyNode._setting_str(model_settings, "clip1", "clip_name1"),
            "clip2": LocalComfyNode._setting_str(model_settings, "clip2", "clip_name2"),
            "vae_name": LocalComfyNode._setting_str(model_settings, "vae_name", "vae"),
            "weight_dtype": LocalComfyNode._setting_str(model_settings, "weight_dtype"),
            "type": LocalComfyNode._setting_str(model_settings, "clip_type"),
        }
        for node in wf.values():
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            for key, value in value_by_input.items():
                if key in inputs and value:
                    inputs[key] = value
        # 算力节点若写了 pruned unet，配合 Turbo LoRA 会采样崩溃；此处统一纠正
        LocalComfyNode._ensure_turbo_compatible_base(wf)

    @staticmethod
    def _select_flux2_klein_reference_chain(wf: dict, count: int) -> None:
        """适配从外部参考软件提取的 FLUX2 Klein 三图参考 API 工作流。

        原工作流固定三张参考图：
        - 0 张参考：退回纯文生图 conditioning
        - 1 张参考：只接第一张参考 latent
        - 2 张参考：接前两张参考 latent
        - 3+ 张参考：接三张参考 latent，多余参考上传但不参与该工作流
        """
        if "128" not in wf or wf.get("128", {}).get("class_type") != "EmptyFlux2LatentImage":
            return
        sampler = wf.get("146")
        if not isinstance(sampler, dict) or sampler.get("class_type") != "KSampler":
            sampler = next((node for node in wf.values() if node.get("class_type") == "KSampler"), None)
        if sampler is None:
            return
        chain_by_count = {
            0: (["108", 0], ["109", 0]),
            1: (["117", 0], ["115", 0]),
            2: (["166", 0], ["167", 0]),
            3: (["180", 0], ["182", 0]),
        }
        positive, negative = chain_by_count[min(max(count, 0), 3)]
        if all(ref[0] in wf for ref in (positive, negative)):
            sampler["inputs"]["positive"] = positive
            sampler["inputs"]["negative"] = negative

    @staticmethod
    def _inject(wf: dict, job: ImageJob, model_settings: dict | None = None) -> dict:
        model_settings = model_settings or {}
        LocalComfyNode._resize_reference_chain(wf, len(job.reference_images))
        LocalComfyNode._select_flux2_klein_reference_chain(wf, len(job.reference_images))
        LocalComfyNode._apply_model_settings(wf, model_settings)
        LocalComfyNode._ensure_turbo_compatible_base(wf)
        # 优先用本次请求（角色页分辨率/步数）；算力节点 width/height/steps 仅作缺省兜底，
        # 避免节点里写死 768×432 覆盖角色超清竖图导致糊脸。
        effective_width = int(job.width) if job.width else (
            LocalComfyNode._setting_int(model_settings, "width", None) or 1024
        )
        effective_height = int(job.height) if job.height else (
            LocalComfyNode._setting_int(model_settings, "height", None) or 1024
        )
        if job.steps is not None:
            effective_steps = int(job.steps)
        else:
            effective_steps = LocalComfyNode._setting_int(model_settings, "steps", None) or 16
        effective_cfg = job.cfg if job.cfg is not None else LocalComfyNode._setting_float(
            model_settings, "cfg", None
        )
        seed = job.seed if job.seed is not None else random.randint(1, 2**31 - 1)

        def find_text_node(ref, seen: set[str] | None = None):
            if not isinstance(ref, list) or not ref or ref[0] not in wf:
                return None
            node_id = ref[0]
            seen = seen or set()
            if node_id in seen:
                return None
            seen.add(node_id)
            node = wf[node_id]
            if node.get("class_type") == "CLIPTextEncode":
                return node
            for value in node.get("inputs", {}).values():
                found = find_text_node(value, seen)
                if found is not None:
                    return found
            return None

        # —— MiniMax H3 文生图（minimax-h3-t2i.api.json / Turbo T8）——
        # 提示词/尺寸在 ImageToVideo 或 AudioConditioningT8；种子 RandomNoise；
        # 步数 BasicScheduler 或 DualClockSamplerT8（Turbo 4 步 + LoRA 时强制 4）
        minimax_nodes = [
            n for n in wf.values() if n.get("class_type") in LocalComfyNode._MINIMAX_COND_TYPES
        ]
        if minimax_nodes:
            for node in minimax_nodes:
                ins = node.setdefault("inputs", {})
                if job.prompt:
                    ins["prompt"] = job.prompt
                ins["width"] = int(effective_width)
                ins["height"] = int(effective_height)
                # 参考图：有图时注入 first_frame（可选图生图/首帧引导；T2I 微视频取首帧）
                if job.reference_images and node.get("class_type") in (
                    "MiniMaxH3ImageToVideo",
                    "MiniMaxH3AudioConditioningT8",
                ):
                    ins["first_frame"] = ["__load_ref_0__", 0]
                    # T8：有首帧时用 I2VA，避免 T2VA + first_frame 校验失败
                    if node.get("class_type") == "MiniMaxH3AudioConditioningT8":
                        ins["task_type"] = "I2VA"
            # 确保参考图 LoadImage 节点存在并连到 first_frame
            if job.reference_images:
                load_id = next(
                    (
                        nid
                        for nid, n in wf.items()
                        if isinstance(n, dict) and n.get("class_type") == "LoadImage"
                    ),
                    None,
                )
                if load_id is None:
                    load_id = "300"
                    wf[load_id] = {
                        "class_type": "LoadImage",
                        "_meta": {"title": "Reference first_frame"},
                        "inputs": {"image": job.reference_images[0], "upload": "image"},
                    }
                else:
                    wf[load_id]["inputs"]["image"] = job.reference_images[0]
                for node in minimax_nodes:
                    if node.get("class_type") in (
                        "MiniMaxH3ImageToVideo",
                        "MiniMaxH3AudioConditioningT8",
                    ):
                        node["inputs"]["first_frame"] = [load_id, 0]
            # Turbo 4 步 LoRA 必须 steps=4，否则 DualClock 会 mat1/mat2 崩溃
            steps = int(effective_steps) if effective_steps else 16
            if LocalComfyNode._has_turbo_lora(wf):
                steps = 4
            for node in wf.values():
                ct = node.get("class_type")
                if ct == "RandomNoise":
                    node.setdefault("inputs", {})["noise_seed"] = int(seed)
                if ct in LocalComfyNode._MINIMAX_STEP_TYPES:
                    ins = node.setdefault("inputs", {})
                    if "steps" in ins:
                        ins["steps"] = steps
                    # MultiRate EXP 用 video_steps / audio_steps
                    if "video_steps" in ins:
                        ins["video_steps"] = steps
                    if "audio_steps" in ins:
                        ins["audio_steps"] = steps
                if ct == "SaveImage":
                    node.setdefault("inputs", {})["filename_prefix"] = "xigua_minimax_h3_t2i"
            # duration 秒：算力设置可覆盖，默认模板 1s
            dur = LocalComfyNode._setting_float(model_settings, "duration", None)
            float_nodes = [n for n in wf.values() if n.get("class_type") == "PrimitiveFloat"]
            for node in float_nodes:
                title = ((node.get("_meta") or {}).get("title") or "")
                if dur is None:
                    break
                if "Duration" in title or "duration" in title or "秒" in title or len(float_nodes) == 1:
                    node.setdefault("inputs", {})["value"] = float(dur)
            load_nodes = [node for node in wf.values() if node.get("class_type") == "LoadImage"]
            for node, image in zip(load_nodes, job.reference_images, strict=False):
                node["inputs"]["image"] = image
            return wf
        # 定位 KSampler，并沿 conditioning 链找到文本节点。
        for node in wf.values():
            if node.get("class_type") in ("KSampler", "KSamplerAdvanced"):
                ins = node["inputs"]
                positive_node = find_text_node(ins.get("positive"))
                if positive_node is not None:
                    positive_node["inputs"]["text"] = job.prompt
                if job.negative is not None:
                    negative_node = find_text_node(ins.get("negative"))
                    if negative_node is not None:
                        negative_node["inputs"]["text"] = job.negative
                ins["seed"] = seed
                if effective_steps:
                    ins["steps"] = effective_steps
                if effective_cfg is not None and "cfg" in ins:
                    ins["cfg"] = effective_cfg
                break
        # 尺寸
        for node in wf.values():
            if node.get("class_type") in ("EmptySD3LatentImage", "EmptyLatentImage", "EmptyFlux2LatentImage"):
                node["inputs"]["width"] = effective_width
                node["inputs"]["height"] = effective_height
        # 多参考工作流中的 LoadImage 按模板顺序注入：场景图在前、人物图在后。
        load_nodes = [node for node in wf.values() if node.get("class_type") == "LoadImage"]
        for node, image in zip(load_nodes, job.reference_images, strict=False):
            node["inputs"]["image"] = image
        return wf
    def _view_url(self, img: dict) -> str:
        q = urlencode(
            {
                "filename": img.get("filename", ""),
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            }
        )
        return f"{self.base_url}/view?{q}"

    @staticmethod
    def _reference_label(source: str) -> str:
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            return Path(unquote(parsed.path)).name or parsed.netloc
        return Path(source).name or "未命名文件"

    async def _read_reference(
        self,
        client: httpx.AsyncClient,
        source: str,
    ) -> tuple[str, bytes, str]:
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            # D2：后端直接抓取 URL，禁止内网段/回环/云元数据地址（SSRF）
            if not host_is_public(parsed.hostname or ""):
                raise ValueError("参考图 URL 必须为公网地址（禁止内网、回环与云元数据地址）")
            response = await client.get(source)
            response.raise_for_status()
            filename = Path(unquote(parsed.path)).name or f"{uuid.uuid4().hex}.png"
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
            return filename, response.content, mime

        # 应用内生成的素材以 /oss/<filename> 形式存入数据库。它是 API 路径，
        # 不是操作系统绝对路径；上传到 ComfyUI 前需映射回本地 OSS 目录。
        decoded_path = unquote(parsed.path).replace("\\", "/")
        if parsed.scheme == "" and decoded_path.startswith("/oss/"):
            oss_root = (settings.data_dir / "oss").resolve()
            path = (oss_root / decoded_path[len("/oss/"):]).resolve()
            try:
                path.relative_to(oss_root)
            except ValueError as exc:
                raise ValueError("非法的 OSS 素材路径") from exc
        else:
            # D2：不再允许任意本地路径（原来 Path(source).expanduser() 可读任意文件）。
            # 参考图只允许 /oss/<...>（上分支已做 relative_to 目录约束）或公网 http(s)。
            raise ValueError("参考图只允许 /oss/<文件名> 或公网 http(s) URL，不接受本地路径")
        if not path.is_file():
            raise FileNotFoundError(f"参考图文件不存在: {self._reference_label(source)}")
        return path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    async def _upload_reference_images(
        self,
        client: httpx.AsyncClient,
        sources: list[str],
    ) -> list[str]:
        uploaded: list[str] = []
        for index, source in enumerate(sources, start=1):
            label = self._reference_label(source)
            try:
                filename, content, mime = await self._read_reference(client, source)
                response = await client.post(
                    f"{self.base_url}/upload/image",
                    headers=self._headers(),
                    files={"image": (filename, content, mime)},
                    data={"overwrite": "true"},
                )
                response.raise_for_status()
                name = response.json().get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError("ComfyUI 未返回上传文件名")
                uploaded.append(name)
            except (OSError, ValueError, httpx.HTTPError) as exc:
                raise RuntimeError(f"第 {index} 张参考图上传失败（{label}）: {exc}") from exc
        return uploaded

    @staticmethod
    def _first_image(outputs: dict) -> dict | None:
        for out in outputs.values():
            for img in out.get("images", []) or []:
                return img
        return None

    async def _download(self, client: httpx.AsyncClient, img: dict) -> Path:
        r = await client.get(self._view_url(img), headers=self._headers())
        r.raise_for_status()
        oss = settings.data_dir / "oss"
        oss.mkdir(parents=True, exist_ok=True)
        raw = img.get("filename") or f"{uuid.uuid4().hex}.png"
        fn = re.sub(r'[<>:"/\\|?*]', "_", raw)  # 去掉 Windows 非法字符，避免 ADS/0字节
        path = oss / fn
        path.write_bytes(r.content)
        return path

    @staticmethod
    def _strip_audio(path: Path) -> Path:
        """去掉 LTX i2v 自带的「模型幻觉音轨」——它与剧本台词无关，会让人物口型/语音对不上。
        台词配音改由 TTS 配音轨负责。无 ffmpeg 时静默跳过、保留原片。"""
        if path.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
            return path
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("未找到 ffmpeg，无法去除 i2v 杂音音轨，保留原片：%s", path.name)
            return path
        tmp = path.with_name(f"{path.stem}__mute{path.suffix}")
        try:
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", str(path), "-c:v", "copy", "-an", str(tmp)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
            )
            if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                path.unlink(missing_ok=True)
                tmp.replace(path)
            else:
                logger.warning("去音轨 ffmpeg 失败(rc=%s)，保留原片", proc.returncode)
                tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("去音轨异常，保留原片：%s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        return path

    @staticmethod
    def _snap32(value: int, minimum: int = 32) -> int:
        """MiniMax H3 宽高要求 32 对齐。"""
        v = max(minimum, int(value))
        return max(minimum, (v // 32) * 32)

    @staticmethod
    def _clamp_h3_canvas(width: int, height: int) -> tuple[int, int]:
        """H3 原生像素面积上限 768×1344；超出则等比缩小并对齐 32。"""
        w = LocalComfyNode._snap32(width)
        h = LocalComfyNode._snap32(height)
        max_pixels = 768 * 1344
        pixels = w * h
        if pixels <= max_pixels:
            return w, h
        scale = (max_pixels / float(pixels)) ** 0.5
        w = LocalComfyNode._snap32(max(32, int(w * scale)))
        h = LocalComfyNode._snap32(max(32, int(h * scale)))
        while w * h > max_pixels:
            if w >= h:
                w = max(32, w - 32)
            else:
                h = max(32, h - 32)
        return w, h

    @staticmethod
    def _format_comfy_http_error(response: httpx.Response, workflow: str) -> str:
        """把 ComfyUI 400 的 node_errors 提炼成可读中文。"""
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            text = (response.text or "").strip()
            return f"ComfyUI HTTP {response.status_code}（工作流 {workflow}）: {text[:500]}"

        parts: list[str] = [f"ComfyUI 拒绝工作流 {workflow} (HTTP {response.status_code})"]
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or err.get("type") or ""
            if msg:
                parts.append(str(msg))
        node_errors = data.get("node_errors") if isinstance(data, dict) else None
        if isinstance(node_errors, dict):
            for node_id, payload in list(node_errors.items())[:6]:
                if not isinstance(payload, dict):
                    continue
                class_type = payload.get("class_type") or ""
                for item in (payload.get("errors") or [])[:3]:
                    if not isinstance(item, dict):
                        continue
                    detail = item.get("details") or item.get("message") or str(item)
                    label = f"节点{node_id}"
                    if class_type:
                        label += f"({class_type})"
                    parts.append(f"{label}: {detail}")
                    # 常见：本机没装 Flux，却在跑 flux 工作流
                    if "not in" in str(detail) and ("flux" in str(detail).lower() or "gguf" in str(detail).lower()):
                        parts.append(
                            "提示：当前 ComfyUI 没有 Flux 模型。请确认算力节点文生图工作流为 "
                            "minimax-h3-t2i.api.json，并重启西瓜后端。"
                        )
        return " | ".join(parts)

    @staticmethod
    def _format_comfy_exec_error(entry: dict, workflow: str) -> str:
        status = entry.get("status") if isinstance(entry, dict) else None
        messages = []
        exception_message = ""
        if isinstance(status, dict):
            for msg in status.get("messages") or []:
                if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_error":
                    payload = msg[1] if isinstance(msg[1], dict) else {}
                    exception_message = str(payload.get("exception_message") or "")
                    node_type = payload.get("node_type") or payload.get("node_id") or ""
                    messages.append(f"{node_type}: {exception_message[:280]}")
                else:
                    messages.append(str(msg)[:400])
        if "2688" in exception_message or (
            "mat1" in exception_message.lower() and "mat2" in exception_message.lower()
        ):
            return (
                f"ComfyUI 执行失败（{workflow}）：Turbo 4 步 LoRA 与 pruned 基模不兼容"
                "（AdaLN 8 vs 2688）。请使用非 pruned 模型 "
                "`minimax_h3_fl2va_int8_convrot.safetensors`，"
                "并在算力节点 model_settings.unet_name 去掉 pruned。"
                f" 原始错误: {exception_message[:200]}"
            )
        if "tuple index out of range" in exception_message and (
            "AVDecode" in str(messages) or "audio" in exception_message.lower()
        ):
            return (
                f"ComfyUI 执行失败（{workflow}）：音视频联合解码在静帧/微视频长度下崩溃。"
                "文生图请用 MiniMaxH3StillDecodeT8（只解视频 latent）。"
                f" 原始错误: {exception_message[:200]}"
            )
        if messages:
            return f"ComfyUI 执行失败（{workflow}）: " + "；".join(messages[:4])
        return f"ComfyUI 执行出错（工作流 {workflow}）"

    async def text2image(self, job: ImageJob, cancel_check: Callable[[], bool] | None = None) -> JobResult:
        try:
            workflow = self._workflow_for_job(job, self.model_settings)
            wf = self._load_workflow(workflow)
        except FileNotFoundError:
            return JobResult("failed", error=f"工作流模板不存在: {self._workflow_for_job(job, self.model_settings)}")
        except Exception as e:  # noqa: BLE001
            return JobResult("failed", error=f"工作流载入失败: {e}")

        client_id = uuid.uuid4().hex
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(900.0, connect=10.0),
                follow_redirects=True,
            ) as c:
                if job.reference_images:
                    uploaded = await self._upload_reference_images(c, job.reference_images)
                    job = replace(job, reference_images=uploaded)
                # MiniMax 宽高对齐 32 + 面积上限
                if any(
                    isinstance(n, dict) and n.get("class_type") in LocalComfyNode._MINIMAX_COND_TYPES
                    for n in wf.values()
                ):
                    cw, ch = self._clamp_h3_canvas(job.width, job.height)
                    job = replace(job, width=cw, height=ch)
                wf = self._inject(wf, job, self.model_settings)
                r = await c.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": wf, "client_id": client_id},
                    headers=self._headers(),
                )
                if r.status_code >= 400:
                    detail = self._format_comfy_http_error(r, workflow)
                    logger.warning("ComfyUI /prompt 失败: %s", detail)
                    return JobResult("failed", error=detail, meta={"workflow": workflow, "status_code": r.status_code})
                prompt_id = r.json().get("prompt_id")
                if not prompt_id:
                    return JobResult("failed", error="ComfyUI 未返回 prompt_id")

                deadline = time.time() + 900
                while time.time() < deadline:
                    if cancel_check is not None and cancel_check():
                        # 用户取消：尽力中断远端任务，不再占着 GPU
                        await self.interrupt()
                        return JobResult(
                            "failed",
                            error="用户取消",
                            meta={"cancelled": True, "prompt_id": prompt_id, "workflow": workflow},
                        )
                    hr = await c.get(f"{self.base_url}/history/{prompt_id}", headers=self._headers())
                    data = hr.json()
                    if prompt_id in data:
                        entry = data[prompt_id]
                        img = self._first_image(entry.get("outputs", {}))
                        if img:
                            path = await self._download(c, img)
                            return JobResult(
                                "completed",
                                image_path=str(path),
                                image_url=self._view_url(img),
                                meta={"prompt_id": prompt_id, "workflow": workflow},
                            )
                        if entry.get("status", {}).get("status_str") == "error":
                            return JobResult(
                                "failed",
                                error=self._format_comfy_exec_error(entry, workflow),
                                meta={"prompt_id": prompt_id, "workflow": workflow},
                            )
                    await asyncio.sleep(1.5)
                return JobResult("failed", error=f"生成超时（工作流 {workflow}）")
        except httpx.HTTPError as e:
            logger.warning("ComfyUI 请求失败: %s", e)
            return JobResult("failed", error=f"连接 ComfyUI 失败: {e}")
        except RuntimeError as e:
            logger.warning("ComfyUI 参考图上传失败: %s", e)
            return JobResult("failed", error=str(e))
        except Exception as e:  # noqa: BLE001
            return JobResult("failed", error=f"{type(e).__name__}: {e}")
    # ===== 图生视频（LTX i2v 等）=====
    @staticmethod
    def _inject_video(
        wf: dict,
        *,
        image_name: str,
        prompt: str | None,
        duration: int | None,
        seed: int,
        audio_name: str | None = None,
        width: int | None = None,
        height: int | None = None,
        filename_prefix: str = "xigua_i2v",
        negative: str | None = None,
        reference_image_names: list[str] | None = None,
    ) -> dict:
        """注入 LoadImage / 多参考 / MiniMax 条件节点 / 时长 / 种子。"""
        ref_names = [n for n in (reference_image_names or []) if n] or (
            [image_name] if image_name else []
        )
        load_nodes = [
            (nid, node)
            for nid, node in sorted(wf.items(), key=lambda kv: str(kv[0]))
            if isinstance(node, dict) and node.get("class_type") == "LoadImage"
        ]
        ref_loads = [
            (nid, node)
            for nid, node in load_nodes
            if "ref" in ((node.get("_meta") or {}).get("title") or "").lower()
            or "picture" in ((node.get("_meta") or {}).get("title") or "").lower()
        ]
        if not ref_loads:
            ref_loads = list(load_nodes)
        for i, name in enumerate(ref_names):
            if i < len(ref_loads):
                ref_loads[i][1].setdefault("inputs", {})["image"] = name
            else:
                new_id = f"refload_{i + 1}"
                wf[new_id] = {
                    "class_type": "LoadImage",
                    "_meta": {"title": f"Ref Picture {i + 1}"},
                    "inputs": {"image": name, "upload": "image"},
                }
                ref_loads.append((new_id, wf[new_id]))
        if not reference_image_names and image_name:
            for node in wf.values():
                if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                    node.setdefault("inputs", {})["image"] = image_name
                    break

        r2v_nodes = [
            node
            for node in wf.values()
            if isinstance(node, dict) and node.get("class_type") in LocalComfyNode._MINIMAX_R2V_TYPES
        ]
        for r2v_node in r2v_nodes:
            if not ref_names:
                break
            ins = r2v_node.setdefault("inputs", {})
            # 只清参考图连线，勿删 ref_image_size（Comfy 必填，误删会 400）
            for key in list(ins.keys()):
                if key == "ref_image_size":
                    continue
                # Comfy V3 Autogrow 正式键：ref_images.ref_image_0 / ref_images.ref_image_1 …
                if key == "ref_images" or key.startswith("ref_images."):
                    del ins[key]
                    continue
                # 兼容旧错误写法 ref_image_0（会被 execute 直接拒收）
                if key.startswith("ref_image_") and key[len("ref_image_") :].isdigit():
                    del ins[key]
            used = ref_loads[: len(ref_names)]
            # 必须用嵌套键 ref_images.ref_image_N，否则 TypeError: unexpected keyword 'ref_image_0'
            for i, (nid, _) in enumerate(used):
                ins[f"ref_images.ref_image_{i}"] = [str(nid), 0]
            if prompt:
                ins["prompt"] = prompt
            if "ref_image_size" not in ins or not ins.get("ref_image_size"):
                ins["ref_image_size"] = "match"
            # T8：多参考必须 Ref2VA；勿用 T2VA（会拒绝 ref media）
            if r2v_node.get("class_type") == "MiniMaxH3AudioConditioningT8":
                task = str(ins.get("task_type") or "auto").lower()
                if task in ("", "auto", "t2va", "t2v"):
                    ins["task_type"] = "Ref2VA"
            logger.info(
                "r2v inject type=%s refs=%s keys=%s prompt_len=%s",
                r2v_node.get("class_type"),
                len(used),
                [k for k in ins if str(k).startswith("ref_")],
                len(prompt or ""),
            )

        # 收集全部 CLIPTextEncode，按标题区分正/负（仅改 text 字段，不破坏连线）
        clip_nodes: list[dict] = [
            node for node in wf.values() if node.get("class_type") == "CLIPTextEncode"
        ]
        positive = negative_node = first = None
        for node in clip_nodes:
            first = first or node
            title = ((node.get("_meta") or {}).get("title") or "").lower()
            if "负" in title or "negative" in title or "neg" in title:
                negative_node = node
            elif "正" in title or "positive" in title or "pos" in title:
                positive = node
        # 无标题时：第一个当正向，第二个当负向（LTX 模板惯例）
        if positive is None and first is not None:
            positive = first
        if negative_node is None and len(clip_nodes) >= 2:
            for node in clip_nodes:
                if node is not positive:
                    negative_node = node
                    break
        if prompt and positive is not None:
            positive["inputs"]["text"] = prompt
        if negative and negative_node is not None:
            negative_node["inputs"]["text"] = negative
        elif negative and positive is not None and negative_node is None:
            # 仅一个 CLIP 节点时无法分负向，把关键负向词并入正向尾部兜底
            positive["inputs"]["text"] = (
                (positive["inputs"].get("text") or "") + "。避免：" + negative[:400]
            ).strip("。")
        # 时长钳到 [2, 5] 秒
        from app.services.video_generation import (
            VIDEO_DEFAULT_DURATION,
            clamp_video_duration,
            minimax_h3_frame_length,
        )

        safe_duration = clamp_video_duration(duration, VIDEO_DEFAULT_DURATION)
        for node in wf.values():
            if node.get("class_type") == "INTConstant" and "秒" in ((node.get("_meta") or {}).get("title", "")):
                node["inputs"]["value"] = int(safe_duration)
                break

        fps = 24
        for node in wf.values():
            ct = node.get("class_type")
            if ct not in ("CreateVideo", "VHS_VideoCombine"):
                continue
            ins = node.get("inputs") or {}
            raw_fps = ins.get("fps") if ct == "CreateVideo" else ins.get("frame_rate")
            if isinstance(raw_fps, (int, float)) and raw_fps > 0:
                fps = int(raw_fps)
                break
        # LTX 等：length ≈ fps*s+1；MiniMax H3：17k+5 网格（5s→124）
        has_minimax = any(
            isinstance(n, dict) and n.get("class_type") in LocalComfyNode._MINIMAX_COND_TYPES
            for n in wf.values()
        )
        frame_length = (
            minimax_h3_frame_length(safe_duration, fps)
            if has_minimax
            else int(safe_duration) * fps + 1
        )
        for node in wf.values():
            ct = node.get("class_type") or ""
            ins = node.get("inputs")
            if not isinstance(ins, dict):
                continue
            is_video_cond = (
                ct in LocalComfyNode._MINIMAX_COND_TYPES
                or "ImageToVideo" in ct
                or "ReferenceToVideo" in ct
                or ct.endswith("ToVideoLatent")
                or "LTX" in ct
            )
            if "length" in ins and not isinstance(ins["length"], list) and is_video_cond:
                ins["length"] = frame_length
            if (
                "prompt" in ins
                and not isinstance(ins["prompt"], list)
                and ct in LocalComfyNode._MINIMAX_COND_TYPES
                and prompt
            ):
                ins["prompt"] = prompt
            if width and "width" in ins and not isinstance(ins["width"], list) and is_video_cond:
                ins["width"] = LocalComfyNode._snap32(int(width))
            if height and "height" in ins and not isinstance(ins["height"], list) and is_video_cond:
                ins["height"] = LocalComfyNode._snap32(int(height))

        # Turbo LoRA：DualClock / MultiRate 固定 4 步
        turbo = LocalComfyNode._has_turbo_lora(wf)
        for node in wf.values():
            ins = node.get("inputs", {})
            if isinstance(ins, dict):
                for key in ("noise_seed", "seed"):
                    if key in ins and not isinstance(ins[key], list):
                        ins[key] = seed
            ct = node.get("class_type")
            if ct == "RandomNoise":
                node.setdefault("inputs", {})["noise_seed"] = int(seed)
            if turbo and ct in LocalComfyNode._MINIMAX_STEP_TYPES:
                step_ins = node.setdefault("inputs", {})
                if "steps" in step_ins and not isinstance(step_ins["steps"], list):
                    step_ins["steps"] = 4
                if "video_steps" in step_ins and not isinstance(step_ins["video_steps"], list):
                    step_ins["video_steps"] = 4
                if "audio_steps" in step_ins and not isinstance(step_ins["audio_steps"], list):
                    step_ins["audio_steps"] = 4
        if audio_name:
            # Keep the supplied TTS latent un-noised while LTX jointly generates the video.
            wf["201"] = {
                "class_type": "LoadAudio",
                "inputs": {"audio": audio_name},
                "_meta": {"title": "TTS audio input"},
            }
            wf["202"] = {
                "class_type": "TrimAudioDuration",
                "inputs": {"audio": ["201", 0], "start_index": 0.0, "duration": float(safe_duration)},
                "_meta": {"title": "Match clip duration"},
            }
            wf["203"] = {
                "class_type": "LTXVAudioVAEEncode",
                "inputs": {"audio": ["202", 0], "audio_vae": ["5", 0]},
                "_meta": {"title": "Encode fixed TTS audio"},
            }
            wf["204"] = {
                "class_type": "SolidMask",
                "inputs": {"value": 0.0, "width": 512, "height": 512},
                "_meta": {"title": "Keep audio latent"},
            }
            wf["205"] = {
                "class_type": "SetLatentNoiseMask",
                "inputs": {"samples": ["203", 0], "mask": ["204", 0]},
                "_meta": {"title": "No noise on supplied audio"},
            }
            if "20" not in wf or wf["20"].get("class_type") != "LTXVConcatAVLatent":
                raise ValueError("LTX2.3 工作流缺少音视频 latent 合并节点")
            wf["20"]["inputs"]["audio_latent"] = ["205", 0]
        # 干净文件名：工作流里的 %date:...% 在 API 模式不会解析，且冒号在 Windows 会被
        # 当成 NTFS ADS 分隔符 → 落成 0 字节文件。统一改成无特殊字符的前缀。
        for node in wf.values():
            if node.get("class_type") in ("SaveVideo", "VHS_VideoCombine", "SaveAnimatedWEBP", "SaveAnimatedPNG"):
                ins = node.get("inputs", {})
                if isinstance(ins, dict) and "filename_prefix" in ins:
                    ins["filename_prefix"] = filename_prefix
        LocalComfyNode._ensure_turbo_compatible_base(wf)
        return wf

    @staticmethod
    def _first_media(outputs: dict, exts: tuple[str, ...]) -> dict | None:
        for out in outputs.values():
            if not isinstance(out, dict):
                continue
            for value in out.values():
                if not isinstance(value, list):
                    continue
                for item in value:
                    if isinstance(item, dict) and str(item.get("filename", "")).lower().endswith(exts):
                        return item
        return None

    @staticmethod
    def _comfy_prompt_error(response: httpx.Response) -> str:
        """解析 Comfy /prompt 400 校验错误，便于前端展示。"""
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return (response.text or "")[:400] or response.reason_phrase
        if not isinstance(data, dict):
            return str(data)[:400]
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        parts: list[str] = []
        if err.get("message"):
            parts.append(str(err["message"]))
        if err.get("details"):
            parts.append(str(err["details"]))
        node_errors = data.get("node_errors")
        if isinstance(node_errors, dict):
            for nid, info in list(node_errors.items())[:4]:
                if not isinstance(info, dict):
                    continue
                ct = info.get("class_type") or nid
                for e in (info.get("errors") or [])[:2]:
                    if isinstance(e, dict):
                        parts.append(f"{ct}: {e.get('message') or ''} {e.get('details') or ''}".strip())
        return "；".join(p for p in parts if p)[:500] or "工作流校验失败"

    async def text2video(
        self,
        *,
        input_image: str | None = None,
        prompt: str | None,
        duration: int | None = None,
        workflow: str = "minimax-h3-r2v.api.json",
        seed: int | None = None,
        input_audio: str | None = None,
        width: int | None = None,
        height: int | None = None,
        negative: str | None = None,
        reference_images: list[str] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> JobResult:
        """多参考图 / 单图 → 视频：上传参考、注入提示词（含台词语音）/时长/种子、轮询下载。

        cancel_check: 轮询中定期调用的取消检查，返回 True 时 POST /interrupt 并提前返回。
        """
        # 节点 model_settings 优先覆盖 i2v/r2v；旧名一律归一到 Turbo r2v
        ms = getattr(self, "model_settings", None) or {}
        if isinstance(ms, dict):
            override = ms.get("workflow_i2v") or ms.get("workflow_video") or ms.get("workflow_r2v")
            if isinstance(override, str) and override.strip():
                workflow = override.strip()
        workflow = self._canonical_workflow(workflow, kind="video")
        if workflow != "minimax-h3-r2v.api.json":
            workflow = "minimax-h3-r2v.api.json"

        try:
            wf = self._load_workflow(workflow)
        except FileNotFoundError:
            return JobResult("failed", error=f"视频工作流模板不存在: {workflow}")
        except Exception as e:  # noqa: BLE001
            return JobResult("failed", error=f"工作流载入失败: {e}")

        is_ltx = False  # LTX 工作流已废弃
        is_r2v = True
        if input_audio:
            return JobResult(
                "failed",
                error="当前工作流不支持独立音频驱动；请把对白写在提示词中由模型生成语音",
            )

        refs = [u for u in (reference_images or []) if u]
        if not refs and input_image:
            refs = [input_image]
        if not refs:
            return JobResult("failed", error="缺少参考图：请提供角色/场景定妆图或镜头参考图")

        client_id = uuid.uuid4().hex
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        prefix = "xigua_ltx" if is_ltx else ("xigua_r2v" if is_r2v else "xigua_i2v")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=10.0), follow_redirects=True) as c:
                uploaded = await self._upload_reference_images(c, refs)
                logger.info("text2video upload refs raw=%s uploaded=%s r2v=%s", refs, uploaded, is_r2v)
                audio_name = None
                if input_audio:
                    audio_name = (await self._upload_reference_images(c, [input_audio]))[0]
                wf = self._inject_video(
                    wf,
                    image_name=uploaded[0],
                    prompt=prompt,
                    duration=duration,
                    seed=seed,
                    audio_name=audio_name,
                    width=width,
                    height=height,
                    filename_prefix=prefix,
                    negative=negative,
                    # 始终注入全部参考文件名（r2v 用多图；i2v 仍会写 LoadImage[0]）
                    reference_image_names=uploaded,
                )
                # 提交前强制纠正 Autogrow 键名（防止热重载/旧缓存仍写出 ref_image_0）
                self._normalize_minimax_r2v_ref_keys(wf)
                try:
                    dbg = settings.data_dir / "comfy_debug" / "last_r2v_prompt.json"
                    dbg.parent.mkdir(parents=True, exist_ok=True)
                    dbg.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
                r = await c.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": wf, "client_id": client_id},
                    headers=self._headers(),
                )
                if r.status_code >= 400:
                    detail = LocalComfyNode._comfy_prompt_error(r)
                    logger.warning("ComfyUI /prompt %s: %s", r.status_code, detail)
                    return JobResult("failed", error=f"ComfyUI 拒绝工作流 ({r.status_code}): {detail}")
                prompt_id = r.json().get("prompt_id")
                if not prompt_id:
                    return JobResult("failed", error="ComfyUI 未返回 prompt_id")

                deadline = time.time() + 1800
                while time.time() < deadline:
                    if cancel_check is not None and cancel_check():
                        # 用户取消：尽力中断远端任务，不再占着 GPU/显存
                        await self.interrupt()
                        return JobResult(
                            "failed",
                            error="用户取消",
                            meta={"cancelled": True, "prompt_id": prompt_id},
                        )
                    hr = await c.get(f"{self.base_url}/history/{prompt_id}", headers=self._headers())
                    data = hr.json()
                    if prompt_id in data:
                        entry = data[prompt_id]
                        media = self._first_media(entry.get("outputs", {}), (".mp4", ".webm", ".mkv", ".gif"))
                        if media:
                            path = await self._download(c, media)
                            # 产品策略：语音由视频模型直接生成/附带，保留成片原生音轨。
                            # 不再剥离音轨，也不再依赖独立 TTS 配音。
                            return JobResult(
                                "completed",
                                image_path=str(path),
                                image_url=self._view_url(media),
                                meta={
                                    "prompt_id": prompt_id,
                                    "audio_driven": bool(input_audio),
                                    "kept_audio": True,
                                    "model_audio": True,
                                },
                            )
                        if entry.get("status", {}).get("status_str") == "error":
                            return JobResult("failed", error="ComfyUI 执行出错（多为模型名不匹配/显存不足）", meta={"prompt_id": prompt_id})
                    await asyncio.sleep(2.0)
                return JobResult("failed", error="视频生成超时")
        except httpx.HTTPError as e:
            logger.warning("ComfyUI 视频请求失败: %s", e)
            return JobResult("failed", error=f"连接 ComfyUI 失败: {e}")
        except RuntimeError as e:
            return JobResult("failed", error=str(e))
        except Exception as e:  # noqa: BLE001
            return JobResult("failed", error=f"{type(e).__name__}: {e}")
