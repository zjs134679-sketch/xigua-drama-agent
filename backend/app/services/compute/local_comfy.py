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
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.compute.base import ComputeNode, ImageJob, JobResult


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

    def _load_workflow(self, name: str) -> dict:
        path: Path = settings.workflows_dir / name
        return json.loads(path.read_text(encoding="utf-8-sig"))

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
        return override or job.workflow

    @staticmethod
    def _apply_model_settings(wf: dict, model_settings: dict) -> None:
        """把 ToonFlow 风格模型设置映射到 ComfyUI API 工作流输入。

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
        effective_width = LocalComfyNode._setting_int(model_settings, "width", job.width) or job.width
        effective_height = LocalComfyNode._setting_int(model_settings, "height", job.height) or job.height
        effective_steps = LocalComfyNode._setting_int(model_settings, "steps", job.steps)
        effective_cfg = LocalComfyNode._setting_float(model_settings, "cfg", job.cfg)

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
                ins["seed"] = job.seed if job.seed is not None else random.randint(1, 2**31 - 1)
                if effective_steps:
                    ins["steps"] = effective_steps
                if effective_cfg is not None and "cfg" in ins:
                    ins["cfg"] = effective_cfg
                break
        # 尺寸
        for node in wf.values():
            if node.get("class_type") in ("EmptySD3LatentImage", "EmptyLatentImage"):
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
            path = Path(source).expanduser()
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

    async def text2image(self, job: ImageJob) -> JobResult:
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
                wf = self._inject(wf, job, self.model_settings)
                r = await c.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": wf, "client_id": client_id},
                    headers=self._headers(),
                )
                r.raise_for_status()
                prompt_id = r.json().get("prompt_id")
                if not prompt_id:
                    return JobResult("failed", error="ComfyUI 未返回 prompt_id")

                deadline = time.time() + 900
                while time.time() < deadline:
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
                            return JobResult("failed", error="ComfyUI 执行出错", meta={"prompt_id": prompt_id})
                    await asyncio.sleep(1.5)
                return JobResult("failed", error="生成超时")
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
    ) -> dict:
        """按节点类型/标题注入：LoadImage=输入图、正向 CLIPTextEncode=提示词、
        标题含「秒」的 INTConstant=时长、noise_seed/seed=随机种子。负向提示词保留工作流自带。"""
        for node in wf.values():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = image_name
                break
        if prompt:
            positive = first = None
            for node in wf.values():
                if node.get("class_type") == "CLIPTextEncode":
                    first = first or node
                    title = (node.get("_meta") or {}).get("title", "")
                    if "正" in title or "positive" in title.lower():
                        positive = node
                        break
            target = positive or first
            if target is not None:
                target["inputs"]["text"] = prompt
        if duration:
            for node in wf.values():
                if node.get("class_type") == "INTConstant" and "秒" in ((node.get("_meta") or {}).get("title", "")):
                    node["inputs"]["value"] = int(duration)
                    break
        for node in wf.values():
            ins = node.get("inputs", {})
            if isinstance(ins, dict):
                for key in ("noise_seed", "seed"):
                    if key in ins and not isinstance(ins[key], list):
                        ins[key] = seed
        if audio_name:
            # Keep the supplied TTS latent un-noised while LTX jointly generates the video.
            wf["201"] = {
                "class_type": "LoadAudio",
                "inputs": {"audio": audio_name},
                "_meta": {"title": "TTS audio input"},
            }
            wf["202"] = {
                "class_type": "TrimAudioDuration",
                "inputs": {"audio": ["201", 0], "start_index": 0.0, "duration": float(duration or 5)},
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
                    ins["filename_prefix"] = "xigua_i2v"
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

    async def text2video(
        self,
        *,
        input_image: str,
        prompt: str | None,
        duration: int | None = None,
        workflow: str = "ltx23-i2v.api.json",
        seed: int | None = None,
        input_audio: str | None = None,
    ) -> JobResult:
        """单图 → 视频：上传输入图、注入提示词/时长/种子、POST /prompt、轮询 /history、下载 mp4。"""
        try:
            wf = self._load_workflow(workflow)
        except FileNotFoundError:
            return JobResult("failed", error=f"视频工作流模板不存在: {workflow}")
        except Exception as e:  # noqa: BLE001
            return JobResult("failed", error=f"工作流载入失败: {e}")

        client_id = uuid.uuid4().hex
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=10.0), follow_redirects=True) as c:
                uploaded = await self._upload_reference_images(c, [input_image])
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
                )
                r = await c.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": wf, "client_id": client_id},
                    headers=self._headers(),
                )
                r.raise_for_status()
                prompt_id = r.json().get("prompt_id")
                if not prompt_id:
                    return JobResult("failed", error="ComfyUI 未返回 prompt_id")

                deadline = time.time() + 1800
                while time.time() < deadline:
                    hr = await c.get(f"{self.base_url}/history/{prompt_id}", headers=self._headers())
                    data = hr.json()
                    if prompt_id in data:
                        entry = data[prompt_id]
                        media = self._first_media(entry.get("outputs", {}), (".mp4", ".webm", ".mkv", ".gif"))
                        if media:
                            path = await self._download(c, media)
                            path = await asyncio.to_thread(self._strip_audio, path)
                            return JobResult(
                                "completed",
                                image_path=str(path),
                                image_url=self._view_url(media),
                                meta={"prompt_id": prompt_id},
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
