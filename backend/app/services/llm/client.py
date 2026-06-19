"""LLM 客户端（OpenAI 兼容 /chat/completions）。

支持 DeepSeek / 通义 / 豆包 / Kimi / 智谱 等：均走 OpenAI 兼容协议，填 base_url + api_key + model 即可。
配置优先级：DB ai_service_configs(service_type='llm', active, 最高 priority) > 环境变量 XIGUA_LLM_*。
"""
from __future__ import annotations

import os

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AiServiceConfig


class LLMNotConfigured(Exception):
    pass


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
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": temperature}
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]
