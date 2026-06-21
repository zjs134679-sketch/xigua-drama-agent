"""提示词润色服务 —— 单条/批量，支持角色/场景/道具/分镜。"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.services.llm.client import chat_text, resolve_llm

POLISH_SYSTEM = """You are a professional AI image prompt optimizer, skilled at converting descriptions into high-quality English Stable Diffusion / Flux prompts.

Optimization rules:
1. Preserve all visual elements from the original (appearance, clothing, scene, lighting, atmosphere)
2. Output in English using natural language description, not comma-separated tag piles
3. Add image quality terms: cinematic lighting, high detail, sharp focus, professional photography
4. Add style consistency marker: consistent style
5. Keep within 40-80 words, concise
6. Output ONLY the optimized prompt itself, no explanation, quotes, prefix or suffix
"""


def polish_prompt(
    db: Session,
    prompt: str,
    asset_type: str = "character",
    context: str | None = None,
    temperature: float = 0.5,
) -> str:
    """润色单条提示词。context 可选，提供角色/场景的额外描述。"""
    base_url, api_key, model = resolve_llm(db)
    type_hints = {
        "character": "Focus on appearance details, clothing details, facial features, hairstyle, expression, pose.",
        "scene": "Focus on location, architecture, lighting, atmosphere, time of day, environment details, no people.",
        "prop": "Focus on object details, material, texture, lighting, still life composition.",
        "storyboard": "Focus on cinematic composition, camera angle, shot type, character action, lighting, mood.",
    }
    hint = type_hints.get(asset_type, type_hints["character"])
    user_msg = f"Asset type: {asset_type}\nHint: {hint}"
    if context:
        user_msg += f"\nContext: {context}"
    user_msg += f"\n\nOriginal prompt:\n{prompt}"

    messages = [
        {"role": "system", "content": POLISH_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    return chat_text(messages, base_url, api_key, model, temperature=temperature).strip()


def batch_polish_prompts(
    db: Session,
    items: list[dict],
    asset_type: str = "storyboard",
    temperature: float = 0.5,
) -> list[dict]:
    """批量润色。items: [{"id": ..., "prompt": "...", "context": "..."}, ...]"""
    base_url, api_key, model = resolve_llm(db)
    items_json = json.dumps([
        {"id": item.get("id"), "original": item.get("prompt", "")}
        for item in items
    ], ensure_ascii=False)
    messages = [
        {"role": "system", "content": POLISH_SYSTEM},
        {"role": "user", "content": (
            f"Please polish each {asset_type} prompt in the list below.\n"
            f"Return JSON: {{\"results\":[{{\"id\":...,\"polished\":\"...\"}}]}}. No explanation.\n\n"
            f"{items_json}"
        )},
    ]
    raw = chat_text(
        messages, base_url, api_key, model, temperature=temperature,
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    return data.get("results") or []
