"""国内云生成 API 适配器。provider 只负责协议差异，节点负责轮询、下载和错误收口。"""
from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.compute.base import ComputeNode, ImageJob, JobResult


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    json: dict | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class PollState:
    status: str  # pending / succeeded / failed
    error: str | None = None


class ProviderAdapter(ABC):
    name: str
    default_base_url: str
    default_model: str
    media_type: str = "image"

    @abstractmethod
    def build_request(self, model: str, job: ImageJob) -> RequestSpec: ...

    @abstractmethod
    def parse_submission(self, payload: dict) -> str: ...

    @abstractmethod
    def build_poll_request(self, task_id: str) -> RequestSpec: ...

    @abstractmethod
    def parse_poll(self, payload: dict) -> PollState: ...

    @abstractmethod
    def extract_result(self, payload: dict) -> str: ...

    def health_request(self) -> RequestSpec:
        return self.build_poll_request("connectivity-check")

    @staticmethod
    def api_error(payload: dict, fallback: str) -> str:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        message = payload.get("message") or output.get("message") or output.get("task_message")
        code = payload.get("code") or output.get("code") or output.get("task_code")
        if message and code:
            return f"{code}: {message}"
        return str(message or code or fallback)


class WanAdapter(ProviderAdapter):
    name = "wan"
    default_base_url = "https://dashscope.aliyuncs.com"
    default_model = "wanx-v1"

    def build_request(self, model: str, job: ImageJob) -> RequestSpec:
        input_data: dict[str, str] = {"prompt": job.prompt}
        if job.negative:
            input_data["negative_prompt"] = job.negative
        parameters: dict[str, object] = {"size": f"{job.width}*{job.height}", "n": 1}
        if job.seed is not None:
            parameters["seed"] = job.seed
        return RequestSpec(
            "POST",
            "/api/v1/services/aigc/text2image/image-synthesis",
            json={"model": model, "input": input_data, "parameters": parameters},
            headers={"X-DashScope-Async": "enable"},
        )

    def parse_submission(self, payload: dict) -> str:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        task_id = output.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(self.api_error(payload, "WAN 未返回 task_id"))
        return task_id

    def build_poll_request(self, task_id: str) -> RequestSpec:
        return RequestSpec("GET", f"/api/v1/tasks/{task_id}")

    def parse_poll(self, payload: dict) -> PollState:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        status = str(output.get("task_status", "")).upper()
        if status == "SUCCEEDED":
            return PollState("succeeded")
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            return PollState("failed", self.api_error(payload, f"WAN 任务状态: {status}"))
        return PollState("pending")

    def extract_result(self, payload: dict) -> str:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        results = output.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            url = results[0].get("url")
            if isinstance(url, str) and url:
                return url
        raise ValueError("WAN 任务成功但未返回图片地址")


def _file_to_data_url(path: Path) -> str:
    import base64
    import mimetypes

    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def resolve_cloud_image_ref(source: str, oss_dir: Path | None = None) -> str:
    """本地 /oss 图 → data URL；已是 http(s)/data 则原样。供云图生视频使用。

    D2：不再接受任意本地绝对路径（原来 p.is_file() 直接读入 base64 发往云端，
    等于把本地文件内容外发）。只允许 /oss/<文件名>（约束在 oss_dir 内）。
    """
    text = (source or "").strip()
    if not text:
        raise ValueError("参考图地址为空")
    if text.startswith("data:") or urlparse(text).scheme in {"http", "https"}:
        return text
    name = Path(text).name
    if not text.startswith("/oss/"):
        raise ValueError("参考图只允许 /oss/<文件名> 或公网 http(s) URL")
    candidates: list[Path] = []
    if oss_dir is not None:
        # basename 约束 + relative_to 目录约束，防止 /oss/../../ 跳出
        cand = (oss_dir / name).resolve()
        try:
            cand.relative_to(oss_dir.resolve())
        except ValueError as exc:
            raise ValueError("非法的 OSS 素材路径") from exc
        candidates.append(cand)
    for cand in candidates:
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                return _file_to_data_url(cand)
        except OSError:
            continue
    raise ValueError(f"无法读取本地参考图: {text[:120]}")


class SeedanceAdapter(ProviderAdapter):
    name = "seedance"
    default_base_url = "https://ark.cn-beijing.volces.com"
    default_model = "doubao-seedance-1-0-pro-250528"
    media_type = "video"

    @staticmethod
    def _ratio(job: ImageJob) -> str:
        if job.width == job.height:
            return "1:1"
        return "16:9" if job.width > job.height else "9:16"

    def build_request(self, model: str, job: ImageJob) -> RequestSpec:
        content: list[dict] = [{"type": "text", "text": job.prompt}]
        for source in job.reference_images or []:
            if not source:
                continue
            content.append({"type": "image_url", "image_url": {"url": source}})
        duration = 5
        if job.duration is not None:
            try:
                duration = max(2, min(int(job.duration), 10))
            except (TypeError, ValueError):
                duration = 5
        body: dict[str, object] = {
            "model": model,
            "content": content,
            "ratio": self._ratio(job),
            "duration": duration,
            "watermark": False,
        }
        if job.seed is not None:
            body["seed"] = job.seed
        return RequestSpec("POST", "/api/v3/contents/generations/tasks", json=body)

    def parse_submission(self, payload: dict) -> str:
        task_id = payload.get("id") or payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(self.api_error(payload, "Seedance 未返回任务 ID"))
        return task_id

    def build_poll_request(self, task_id: str) -> RequestSpec:
        return RequestSpec("GET", f"/api/v3/contents/generations/tasks/{task_id}")

    def parse_poll(self, payload: dict) -> PollState:
        status = str(payload.get("status", "")).lower()
        if status in {"succeeded", "success", "completed"}:
            return PollState("succeeded")
        if status in {"failed", "cancelled", "canceled", "expired"}:
            return PollState("failed", self.api_error(payload, f"Seedance 任务状态: {status}"))
        return PollState("pending")

    def extract_result(self, payload: dict) -> str:
        candidates = [payload.get("content"), payload.get("output"), payload.get("result")]
        for candidate in candidates:
            if isinstance(candidate, dict):
                url = candidate.get("video_url") or candidate.get("url")
                if isinstance(url, str) and url:
                    return url
        raise ValueError("Seedance 任务成功但未返回视频地址")


PROVIDERS: dict[str, ProviderAdapter] = {
    "wan": WanAdapter(),
    "seedance": SeedanceAdapter(),
}


class CloudApiNode(ComputeNode):
    type = "cloud_api"

    def __init__(
        self,
        provider: str,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> None:
        self.provider = (provider or "").strip().lower()
        self.adapter = PROVIDERS.get(self.provider)
        default_url = self.adapter.default_base_url if self.adapter else ""
        super().__init__(base_url or default_url, token)
        self.api_key = api_key
        self.model = model or (self.adapter.default_model if self.adapter else "")
        self.last_error: str | None = None

    def _headers(self) -> dict[str, str]:
        credential = self.api_key or self.token
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def health(self) -> bool:
        if self.adapter is None:
            self.last_error = f"不支持的云 provider: {self.provider or '未配置'}"
            return False
        if not (self.api_key or self.token):
            self.last_error = "云节点未配置 API key"
            return False
        spec = self.adapter.health_request()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=5.0),
                follow_redirects=True,
            ) as client:
                response = await client.request(
                    spec.method,
                    self._url(spec.path),
                    headers={**self._headers(), **(spec.headers or {})},
                    json=spec.json,
                )
            if response.status_code in {401, 403}:
                self.last_error = f"云节点鉴权失败（HTTP {response.status_code}）"
                return False
            if response.status_code >= 500:
                self.last_error = f"云节点服务异常（HTTP {response.status_code}）"
                return False
            self.last_error = None
            return True
        except httpx.RequestError as exc:
            self.last_error = f"连接云节点失败: {exc}"
            return False
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"云节点检测失败: {type(exc).__name__}: {exc}"
            return False

    async def _send(self, client: httpx.AsyncClient, spec: RequestSpec) -> dict:
        response = await client.request(
            spec.method,
            self._url(spec.path),
            headers={**self._headers(), **(spec.headers or {})},
            json=spec.json,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("云 API 返回了无效 JSON")
        return payload

    @staticmethod
    async def _download(client: httpx.AsyncClient, url: str, media_type: str) -> Path:
        response = await client.get(url)
        response.raise_for_status()
        suffix = Path(urlparse(url).path).suffix.lower()
        if not suffix or len(suffix) > 6:
            content_type = response.headers.get("content-type", "").lower()
            suffix = ".mp4" if media_type == "video" or "video/" in content_type else ".png"
        oss = settings.data_dir / "oss"
        oss.mkdir(parents=True, exist_ok=True)
        path = oss / f"{uuid.uuid4().hex}{suffix}"
        path.write_bytes(response.content)
        return path

    async def _run_async_job(self, job: ImageJob) -> JobResult:
        if self.adapter is None:
            return JobResult("failed", error=f"不支持的云 provider: {self.provider or '未配置'}")
        if not (self.api_key or self.token):
            return JobResult("failed", error="云节点未配置 API key")
        try:
            submit = self.adapter.build_request(self.model, job)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                follow_redirects=True,
            ) as client:
                task_id = self.adapter.parse_submission(await self._send(client, submit))
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    payload = await self._send(client, self.adapter.build_poll_request(task_id))
                    state = self.adapter.parse_poll(payload)
                    if state.status == "failed":
                        return JobResult(
                            "failed",
                            error=state.error or f"{self.provider} 云任务失败",
                            meta={"provider": self.provider, "task_id": task_id},
                        )
                    if state.status == "succeeded":
                        result_url = self.adapter.extract_result(payload)
                        path = await self._download(client, result_url, self.adapter.media_type)
                        return JobResult(
                            "completed",
                            image_path=str(path),
                            image_url=result_url,
                            meta={
                                "provider": self.provider,
                                "model": self.model,
                                "task_id": task_id,
                                "media_type": self.adapter.media_type,
                            },
                        )
                    await asyncio.sleep(2)
                return JobResult(
                    "failed",
                    error=f"{self.provider} 云任务生成超时",
                    meta={"provider": self.provider, "task_id": task_id},
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("云 API 请求失败 provider=%s status=%s", self.provider, status)
            return JobResult("failed", error=f"{self.provider} 云 API 请求失败（HTTP {status}）")
        except httpx.RequestError as exc:
            logger.warning("云 API 连接失败 provider=%s: %s", self.provider, exc)
            return JobResult("failed", error=f"连接 {self.provider} 云 API 失败: {exc}")
        except (OSError, ValueError) as exc:
            return JobResult("failed", error=f"{self.provider} 云任务处理失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("云 API 未预期错误 provider=%s", self.provider)
            return JobResult("failed", error=f"{self.provider} 云节点异常: {type(exc).__name__}: {exc}")

    async def text2image(self, job: ImageJob) -> JobResult:
        if self.adapter is not None and self.adapter.media_type == "video":
            return JobResult(
                "failed",
                error=f"{self.provider} 是视频模型，不能出静帧图。请配置通义万相（wan）作出图节点",
            )
        return await self._run_async_job(job)

    async def text2video(
        self,
        *,
        input_image: str,
        prompt: str | None,
        duration: int | None = None,
        workflow: str = "",
        seed: int | None = None,
        input_audio: str | None = None,
        width: int | None = None,
        height: int | None = None,
        negative: str | None = None,
    ) -> JobResult:
        """云图生视频（当前支持 Seedance）。本地首帧会转 data URL，无需公网图床。"""
        _ = workflow, input_audio, negative  # 云路径不走 Comfy 工作流 / 口型
        if self.adapter is None:
            return JobResult("failed", error=f"不支持的云 provider: {self.provider or '未配置'}")
        if self.adapter.media_type != "video":
            return JobResult(
                "failed",
                error=f"{self.provider} 不能出视频。请配置豆包 Seedance 作出片节点，或使用本地 ComfyUI",
            )
        try:
            ref = resolve_cloud_image_ref(input_image, settings.data_dir / "oss")
        except ValueError as exc:
            return JobResult("failed", error=str(exc))
        sec = max(2, min(int(duration or 5), 10))
        job = ImageJob(
            prompt=(prompt or "cinematic motion, smooth camera").strip(),
            width=int(width or 720),
            height=int(height or 1280),
            seed=seed,
            reference_images=[ref],
            duration=sec,
        )
        return await self._run_async_job(job)
