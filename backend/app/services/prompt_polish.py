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
2. 角色三视图（正面/侧面/背面 3 个全身人物）或清晰全身参考，同一张脸、同一发型、同一身材、同一套服装。
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

# 道具专用：禁止走通用润色（通用规则会保留「场景/光线/气氛」）
PROP_POLISH_SYSTEM = """你是西瓜短剧 Agent 的道具定装提示词架构师。

把用户描述改写成「单件道具影棚定装图」中文提示词，结构必须如下：
1. 开头写清：西瓜短剧写实道具设定图，单件道具居中。
2. 只写道具本体：名称、形状、颜色、材质、纹理、磨损、反光、新旧程度。
3. 背景必须且只能是：浅灰干净影棚背景，背景简洁无杂物，浅灰色纯净背景（与人物定装相同影棚）。
4. 结尾必须再次强调：无桌面、无台面、无地面、无墙面、无房间、无战场、无风景、无人物、无手。

绝对禁止出现：
- 木桌、柜台、台面、地板、墙、房间、大帐、军营、战场、街道、风景
- 背景虚化成环境、黄昏、窗光环境叙事
- 人物、士兵、手、握持、跪姿、使用中的道具
- 英文句子（专有名词除外）

规则：
- 只保留与物件本身有关的细节；原文里的场景叙事一律删掉。
- 不要编造使用场景。
- 不要输出解释、JSON、引号、Markdown。
- 只输出一段中文提示词，约 80 到 180 个中文字符。
"""


def polish_prompt(
    db: Session,
    prompt: str,
    asset_type: str = "character",
    context: str | None = None,
    temperature: float = 0.5,
    *,
    drama_id: int | None = None,
) -> str:
    """润色单条提示词。context 可选，提供角色/场景的额外描述。"""
    base_url, api_key, model = resolve_llm(db)
    style_note = ""
    try:
        from app.models.domain import Drama
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_IDENTITY, STAGE_SCENE_ENV, STAGE_SHOT_IMAGE

        # 道具不用画风手册前缀：手册常含「电影光影/空间」会诱导场景
        if asset_type != "prop":
            stage = {
                "character": STAGE_IDENTITY,
                "scene": STAGE_SCENE_ENV,
                "storyboard": STAGE_SHOT_IMAGE,
            }.get(asset_type, STAGE_SHOT_IMAGE)
            drama = db.get(Drama, drama_id) if drama_id is not None else None
            contract = compose(db=db, drama=drama, stage=stage)
            style_note = "\n\n" + contract.system_prefix()
            if asset_type == "scene":
                style_note += "\n场景必须是无人环境空镜。"
    except Exception:  # noqa: BLE001
        style_note = ""
    if asset_type == "character":
        user_msg = "素材类型：人物\n"
        if context:
            user_msg += f"人物上下文：{context}\n"
        user_msg += f"\n原始提示词：\n{prompt}"
        messages = [
            {"role": "system", "content": CHARACTER_POLISH_SYSTEM + style_note},
            {"role": "user", "content": user_msg},
        ]
        return chat_text(messages, base_url, api_key, model, temperature=temperature).strip()
    if asset_type == "prop":
        from app.services.asset_generation import finalize_prop_user_prompt, scrub_prop_environment_from_prompt

        # 先洗一遍再给 LLM，减少它续写环境
        seed = scrub_prop_environment_from_prompt(prompt) or (prompt or "").strip()
        user_msg = "素材类型：道具定装（影棚单件）\n"
        if context:
            user_msg += f"道具上下文（仅供识别物件，不要写使用场景）：{context}\n"
        user_msg += (
            f"\n原始描述：\n{seed}\n\n"
            "请只输出物件本体 + 浅灰影棚背景，不要任何环境叙事。"
        )
        messages = [
            {"role": "system", "content": PROP_POLISH_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        text = chat_text(messages, base_url, api_key, model, temperature=min(temperature, 0.35)).strip()
        return finalize_prop_user_prompt(text) or finalize_prop_user_prompt(seed)
    type_hints = {
        "character": "重点写外貌、服装、五官、发型、表情、姿态。",
        "scene": "重点写地点、建筑、光线、气氛、时间、环境细节，明确不要人物。",
        "prop": "重点写物体本身的外形、材质、纹理、颜色、磨损；必须纯净影棚浅灰/纯白无缝背景；禁止木桌台面、房间、战场、风景、人物、手。",
        "storyboard": "重点写电影构图、机位、景别、角色动作、光线、情绪。",
    }
    hint = type_hints.get(asset_type, type_hints["character"])
    user_msg = f"素材类型：{asset_type}\n要求：{hint}\n必须输出中文提示词。"
    if context:
        user_msg += f"\n上下文：{context}"
    user_msg += f"\n\n原始提示词：\n{prompt}"

    messages = [
        {"role": "system", "content": POLISH_SYSTEM + style_note},
        {"role": "user", "content": user_msg},
    ]
    text = chat_text(messages, base_url, api_key, model, temperature=temperature).strip()
    if asset_type == "scene":
        from app.services.asset_generation import scrub_people_from_prompt

        text = scrub_people_from_prompt(text) or text
    return text


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
