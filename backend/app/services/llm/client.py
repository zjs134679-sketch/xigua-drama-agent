"""LLM 客户端（OpenAI 兼容 /chat/completions）。

支持 DeepSeek / 通义 / 豆包 / Kimi / 智谱 等：均走 OpenAI 兼容协议，填 base_url + api_key + model 即可。
配置优先级：DB ai_service_configs(service_type='llm', active, 最高 priority) > 环境变量 XIGUA_LLM_*。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AiServiceConfig


class LLMNotConfigured(Exception):
    pass


# 重试配置：仅对可恢复的错误重试（429 限流 / 5xx / 超时 / 连接错误）
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # 指数退避基数（秒）：1s、2s、4s


def _is_retryable(exc: BaseException) -> bool:
    """判断 LLM 调用异常是否值得重试。4xx（除 429）、参数错误、响应解析错误等直接抛。"""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        return status == 429 or (isinstance(status, int) and status >= 500)
    return False


def resolve_llm(db: Session | None) -> tuple[str, str, str]:
    if db is not None:
        row = db.scalars(
            select(AiServiceConfig)
            .where(AiServiceConfig.service_type == "llm", AiServiceConfig.is_active.is_(True))
            .order_by(AiServiceConfig.priority.desc())
        ).first()
        if row and row.base_url and row.api_key:
            return row.base_url, row.api_key, row.model or "deepseek-chat"

    base = os.environ.get("XIGUA_LLM_BASE_URL")
    key = os.environ.get("XIGUA_LLM_API_KEY")
    model = os.environ.get("XIGUA_LLM_MODEL", "deepseek-chat")
    if base and key:
        return base, key, model

    raise LLMNotConfigured("未配置 LLM：ai_service_configs 无 active 的 llm 配置，且未设 XIGUA_LLM_BASE_URL/API_KEY")


def chat(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.7,
    timeout: float = 120.0,
    response_format: dict | None = None,
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
    max_retries: int = _MAX_RETRIES,
) -> dict:
    """同步调用，返回完整响应字典 {content, tool_calls, finish_reason}。

    对 429/5xx/超时/连接错误自动指数退避重试（默认 3 次），其他错误直接抛出。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict = {"model": model, "messages": messages, "temperature": temperature}
    if response_format:
        body["response_format"] = response_format
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if max_tokens is not None and max_tokens > 0:
        body["max_tokens"] = int(max_tokens)
    attempts = max(1, max_retries)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(url, json=body, headers=headers)
                r.raise_for_status()
                choice = r.json()["choices"][0]
            msg = choice.get("message", {})
            return {
                "content": msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or [],
                "finish_reason": choice.get("finish_reason", "stop"),
            }
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc) or attempt >= attempts - 1:
                raise
            time.sleep(_RETRY_BASE_DELAY * (2**attempt))
    raise last_exc  # pragma: no cover - 上面循环必定 return 或 raise


def chat_text(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.7,
    timeout: float = 120.0,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    max_retries: int = _MAX_RETRIES,
) -> str:
    """同步调用，仅返回文本内容（兼容旧接口）。"""
    return chat(
        messages,
        base_url,
        api_key,
        model,
        temperature,
        timeout,
        response_format,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )["content"]


async def chat_stream(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.7,
    timeout: float = 180.0,
    response_format: dict | None = None,
    tools: list[dict] | None = None,
    max_retries: int = _MAX_RETRIES,
) -> AsyncGenerator[dict, None]:
    """流式调用，逐块 yield {type: "text"|"tool_call"|"done", content, ...}。

    建连阶段的 429/5xx/超时/连接错误自动指数退避重试（默认 3 次）；
    一旦已吐出内容则不再重试（流无法重播），直接抛给上层。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if response_format:
        body["response_format"] = response_format
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    attempts = max(1, max_retries)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        yielded_any = False
        try:
            tool_call_buffer: dict[int, dict] = {}
            async with httpx.AsyncClient(timeout=timeout) as c:
                async with c.stream("POST", url, json=body, headers=headers) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        finish = chunk.get("choices", [{}])[0].get("finish_reason")

                        if delta.get("content"):
                            yielded_any = True
                            yield {"type": "text", "content": delta["content"]}

                        if delta.get("tool_calls"):
                            yielded_any = True
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in tool_call_buffer:
                                    tool_call_buffer[idx] = {
                                        "id": tc.get("id") or "",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                buf = tool_call_buffer[idx]
                                if tc.get("id"):
                                    buf["id"] = tc["id"]
                                if tc.get("function", {}).get("name"):
                                    buf["function"]["name"] += tc["function"]["name"]
                                if tc.get("function", {}).get("arguments"):
                                    buf["function"]["arguments"] += tc["function"]["arguments"]

                        if finish == "tool_calls":
                            for buf in sorted(tool_call_buffer.values(), key=lambda b: b["id"]):
                                try:
                                    buf["function"]["arguments_parsed"] = json.loads(buf["function"]["arguments"])
                                except json.JSONDecodeError:
                                    buf["function"]["arguments_parsed"] = {}
                                yield {"type": "tool_call", **buf}

                    yield {"type": "done"}
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if yielded_any or not _is_retryable(exc) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
    raise last_exc  # pragma: no cover - 上面循环必定 return 或 raise
