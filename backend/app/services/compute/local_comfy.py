"""本地 ComfyUI 适配器 —— 移植 xigua-drama 已验证的调用序列：
载入 API 工作流 → 注入 positive/seed/尺寸 → POST /prompt → 轮询 /history → /view 下载。
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import random
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

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        super().__init__(base_url or settings.comfyui_base_url, token)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.base_url}/system_stats", headers=self._headers())
                return r.status_code == 200
        except Exception:
            return False

    def _load_workflow(self, name: str) -> dict:
        path: Path = settings.workflows_dir / name
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _inject(wf: dict, job: ImageJob) -> dict:
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
                if job.steps:
                    ins["steps"] = job.steps
                break
        # 尺寸
        for node in wf.values():
            if node.get("class_type") in ("EmptySD3LatentImage", "EmptyLatentImage"):
                node["inputs"]["width"] = job.width
                node["inputs"]["height"] = job.height
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
        fn = img.get("filename") or f"{uuid.uuid4().hex}.png"
        path = oss / fn
        path.write_bytes(r.content)
        return path

    async def text2image(self, job: ImageJob) -> JobResult:
        try:
            wf = self._load_workflow(job.workflow)
        except FileNotFoundError:
            return JobResult("failed", error=f"工作流模板不存在: {job.workflow}")
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
                wf = self._inject(wf, job)
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
                                meta={"prompt_id": prompt_id},
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
