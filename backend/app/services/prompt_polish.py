"""提示词润色服务 —— 单条/批量，支持角色/场景/道具/分镜。"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.services.llm.client import chat_text, resolve_llm

POLISH_SYSTEM = """你是西瓜短剧 Agent 的 AI 图片提示词优化器，负责把用户描述改写成高质量中文提示词。

优化规则：
1. 保留原始描述中的所有视觉元素：外貌、服装、场景、光线、气氛、动作、道具。
2. 必须输出中文自然语言提示词，不要输出英文，不要中英混杂，不要标签堆砌。
3. 补充画质词：电影感光线、高细节、清晰对焦、真实摄影质感、统一风格。
4. 保持简洁，通常 80 到 160 个中文字符。
5. 只输出优化后的提示词本身，不要解释、不要引号、不要前缀、不要 Markdown。
"""

CHARACTER_POLISH_SYSTEM = """你是西瓜短剧 Agent 的人物资产提示词架构师。

请把用户的人物描述改写成可复用的中文真人角色参考图提示词。
提示词必须包含这些结构：
1. 西瓜短剧写实人物设定图 / 真人选角参考照。
2. 角色四视图或清晰全身参考，同一张脸、同一发型、同一身材、同一套服装。
3. 身份：性别、年龄、体型、脸部、肤色、气质。
4. 发型：颜色、长短、造型、轮廓。
5. 服装：完整穿搭、布料、颜色、领口、袖子、腰部、鞋子；古代/年代剧要写清时代准确性。
6. 中性站姿，浅灰干净影棚背景；除非原文明确需要，否则不要道具、不要额外人物。
7. 高细节、清晰对焦、短剧写实摄影质感。

规则：
- 保留原始描述和上下文中的每一个具体细节。
- 不要编造无关服装、武器、背景或额外人物。
- 不要输出标签、解释、JSON、引号或 Markdown。
- 只输出一段中文提示词，约 120 到 260 个中文字符。
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
    if asset_type == "character":
        user_msg = "素材类型：人物\n"
        if context:
            user_msg += f"人物上下文：{context}\n"
        user_msg += f"\n原始提示词：\n{prompt}"
        messages = [
            {"role": "system", "content": CHARACTER_POLISH_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        return chat_text(messages, base_url, api_key, model, temperature=temperature).strip()
    type_hints = {
        "character": "重点写外貌、服装、五官、发型、表情、姿态。",
        "scene": "重点写地点、建筑、光线、气氛、时间、环境细节，明确不要人物。",
        "prop": "重点写物体细节、材质、纹理、光线、静物构图。",
        "storyboard": "重点写电影构图、机位、景别、角色动作、光线、情绪。",
    }
    hint = type_hints.get(asset_type, type_hints["character"])
    user_msg = f"素材类型：{asset_type}\n要求：{hint}\n必须输出中文提示词。"
    if context:
        user_msg += f"\n上下文：{context}"
    user_msg += f"\n\n原始提示词：\n{prompt}"

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
            f"请把下面每条 {asset_type} 提示词润色为中文提示词。\n"
            f"返回 JSON：{{\"results\":[{{\"id\":...,\"polished\":\"中文提示词\"}}]}}。不要解释。\n\n"
            f"{items_json}"
        )},
    ]
    raw = chat_text(
        messages, base_url, api_key, model, temperature=temperature,
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    return data.get("results") or []
