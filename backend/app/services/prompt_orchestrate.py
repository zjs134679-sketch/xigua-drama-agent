"""出图前提示词编排 —— 接入 xg_prompt_grid / xg_h3_video_prompt。

- 图像提示词（角色/场景/分镜静帧）：xg_prompt_grid
- 视频提示词（MiniMax H3 文生视频/图生视频）：xg_h3_video_prompt

失败时回退原文，不阻断生成。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import logger
from app.services.agents.script_agent import load_skill
from app.services.llm.client import LLMNotConfigured, chat_text, resolve_llm


def _ref_templates(kind: str) -> str:
    from pathlib import Path

    base = Path(__file__).resolve().parent / "agents" / "skills" / "xg_prompt_grid" / "reference"
    name = {"character": "character.md", "scene": "scene.md", "storyboard": "shot.md"}.get(kind, "shot.md")
    path = base / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _h3_ref_text(name: str) -> str:
    """加载 xg_h3_video_prompt 的 reference 文件。"""
    from pathlib import Path

    base = Path(__file__).resolve().parent / "agents" / "skills" / "xg_h3_video_prompt" / "reference"
    path = base / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def orchestrate_image_prompt(
    db: Session,
    *,
    kind: str,
    raw_prompt: str,
    context: str | None = None,
    duration_sec: int | None = None,
    temperature: float = 0.35,
) -> str:
    """kind: character | scene | storyboard | prop

    返回编排后的中文提示词；LLM 不可用时返回原 prompt。
    """
    raw = (raw_prompt or "").strip()
    if not raw:
        return raw
    # 已足够长且像成品时跳过（省 token）
    if len(raw) >= 120 and "禁止" in raw and ("光线" in raw or "景别" in raw or "设定" in raw):
        return raw

    try:
        base_url, api_key, model = resolve_llm(db)
    except LLMNotConfigured:
        return raw

    try:
        skill = load_skill("xg_prompt_grid")
    except FileNotFoundError:
        return raw

    kind_label = {
        "character": "角色定装",
        "scene": "场景空镜",
        "storyboard": "分镜静帧",
        "prop": "道具特写",
    }.get(kind, "分镜静帧")
    ref = _ref_templates("storyboard" if kind == "prop" else kind)
    dur_note = ""
    if kind == "storyboard" and duration_sec:
        dur_note = f"\n本镜出片时长约 {int(duration_sec)} 秒，静帧只需写起始姿态，不要写超过该时长的动作过程。"

    # 角色/场景类：叠加 H3 提示词框架（六层控制 + 身份锁定），提升定装/造景质量
    h3_boost = ""
    if kind in ("character", "scene"):
        h3_framework = _h3_ref_text("prompt-framework.md")
        h3_capability = _h3_ref_text("capability-map.md")
        if h3_framework or h3_capability:
            boost_parts: list[str] = []
            if h3_framework:
                boost_parts.append(h3_framework[:1200])
            if h3_capability:
                # 只取角色/场景相关路由规则
                boost_parts.append(h3_capability[:800])
            h3_boost = (
                "\n\n【H3 提示词工程增强（来自 xg_h3_video_prompt）】\n"
                + "\n".join(boost_parts)
            )

    system = (
        skill
        + "\n\n"
        + (ref + "\n\n" if ref else "")
        + h3_boost
        + f"\n当前任务类型：{kind_label}。{dur_note}\n"
        "只输出一条可直接文生图的中文提示词，不要解释、不要 JSON、不要 Markdown。"
    )
    user = f"原始描述：\n{raw}"
    if context:
        user = f"上下文：\n{context}\n\n" + user

    try:
        out = chat_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            base_url,
            api_key,
            model,
            temperature=temperature,
        ).strip()
        if not out or len(out) < 8:
            return raw
        # 去掉模型爱加的引号
        if (out.startswith("「") and out.endswith("」")) or (out.startswith('"') and out.endswith('"')):
            out = out[1:-1].strip()
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_orchestrate 失败 kind=%s: %s", kind, exc)
        return raw


def orchestrate_video_prompt(
    db: Session,
    *,
    raw_prompt: str,
    context: str | None = None,
    duration_sec: int = 5,
    aspect_ratio: str = "16:9",
    mode: str = "reference-to-video",
    image_count: int = 0,
    video_count: int = 0,
    has_dialogue: bool = False,
    temperature: float = 0.35,
) -> str:
    """用 xg_h3_video_prompt 技能编排视频提示词。

    mode: text-to-video | reference-to-video | first-last-frame | video-editing
    失败时回退原文，不阻断出片。
    """
    raw = (raw_prompt or "").strip()
    if not raw:
        return raw

    # 跳过已足够完整的成品提示词（≥200 字且含时间片）
    if len(raw) >= 200 and ("0-" in raw or "秒：" in raw or "秒:" in raw):
        return raw

    try:
        base_url, api_key, model = resolve_llm(db)
    except LLMNotConfigured:
        return raw

    try:
        skill = load_skill("xg_h3_video_prompt")
    except FileNotFoundError:
        return raw

    # 加载 H3 参考规则（参数边界 + 短剧核心路由）
    rules = _h3_ref_text("official-rules.md")
    capability = _h3_ref_text("capability-map.md")
    framework = _h3_ref_text("prompt-framework.md")

    ref_blocks: list[str] = []
    if rules:
        ref_blocks.append(f"【H3 参数边界】\n{rules[:1200]}")
    if capability:
        ref_blocks.append(f"【短剧路由规则】\n{capability[:1500]}")
    ref_blocks.append("【关键原则】六层控制：先写不变量（人物身份锁定）→ 事件轴 → 摄影与调度 → 质感 → 声音。")

    dur_note = f"本镜 {duration_sec} 秒（MiniMax H3 图生视频），{aspect_ratio} 画幅。"
    mode_note = {
        "text-to-video": "纯文本生成视频，无参考素材。",
        "reference-to-video": f"多参考图生视频：{image_count} 张角色/场景定妆图 + 可选动作参考。",
        "first-last-frame": "首尾帧模式：用首帧/尾帧图锁定起幅落幅。",
        "video-editing": "精准编辑：在参考视频基础上做局部修改。",
    }.get(mode, "多参考图生视频。")

    dialogue_note = ""
    if has_dialogue:
        dialogue_note = (
            "本镜含对白：画面层只写说话状态与口型，"
            "具体台词由下游【必须照念的对白】块单独注入，你不要改写或删除台词。"
        )
    else:
        dialogue_note = "本镜无对白：不要写说话、念白或即兴对话。"

    ctx = f"上下文：\n{context}" if context else ""

    # 反「画面字幕」≠ 禁止音频层台词（台词由下游单独块注入）
    anti_text_rules = (
        "\n\n【硬性禁止 —— 最高优先级】\n"
        "1. 画面层不要写可渲染为字幕/花字/标题/UI 的文字；\n"
        "2. 不要把对白写成「屏幕上出现字幕：…」；\n"
        "3. 画面描述可用「角色张嘴说话/倾听」等状态，不要即兴编造具体台词内容；\n"
        "4. 具体台词留给下游注入，你输出的视觉描述里不要胡编角色说了什么；\n"
        "5. 无对白镜头不要写任何人在说话。"
    )

    system = (
        skill
        + "\n\n"
        + "\n\n".join(ref_blocks)
        + "\n\n"
        + f"【本次任务参数】{dur_note}{mode_note}{dialogue_note}\n"
        + "请把原始视频描述改写为一条可直接送 MiniMax H3 的视频提示词。\n"
        + anti_text_rules
        + "\n只输出最终提示词文本，不要 JSON、不要 Markdown 标题、不要解释。"
    )
    user = f"原始视频描述：\n{raw}{ctx}"

    try:
        out = chat_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            base_url,
            api_key,
            model,
            temperature=temperature,
        ).strip()
        if not out or len(out) < 10:
            return raw
        if (out.startswith("「") and out.endswith("」")) or (out.startswith('"') and out.endswith('"')):
            out = out[1:-1].strip()
        # 硬截断到 7000 字符（H3 上限）
        if len(out) > 6800:
            out = out[:6800].rstrip("，,；; …") + "…"
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrate_video_prompt 失败: %s", exc)
        return raw


def align_video_prompt_to_duration(video_prompt: str | None, duration_sec: int) -> str:
    """把 video_prompt 里的时间片裁到 duration 秒内，并改写超界区间。"""
    import re

    text = (video_prompt or "").strip()
    if not text:
        return text
    dur = max(2, min(int(duration_sec or 5), 5))

    def _clip_range(match: re.Match[str]) -> str:
        a = float(match.group(1))
        b = float(match.group(2))
        if a >= dur:
            return ""  # 整段超出则删
        b2 = min(b, float(dur))
        a2 = min(a, float(dur))
        if b2 <= a2:
            return ""
        # 整数秒显示
        if a2 == int(a2) and b2 == int(b2):
            return f"{int(a2)}-{int(b2)}秒"
        return f"{a2:.0f}-{b2:.0f}秒"

    # 0-2秒： / 0–2秒 / 0~2s
    pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*[-–—~～到至]\s*(\d+(?:\.\d+)?)\s*(?:秒|s|S)"
    )
    parts: list[str] = []
    last = 0
    for m in pattern.finditer(text):
        parts.append(text[last : m.start()])
        repl = _clip_range(m)
        if repl:
            parts.append(repl)
        last = m.end()
    parts.append(text[last:])
    out = "".join(parts)
    out = re.sub(r"[；;]\s*[；;]", "；", out)
    out = re.sub(r"\n{3,}", "\n", out).strip()
    # 若没有任何时间片，补一句时长声明
    if not pattern.search(out) and out:
        out = f"0-{dur}秒：{out}"
    # 超长提示词截断尾部（避免写到 5s 而只出 3s 的冗余）
    if len(out) > 400:
        out = out[:400].rstrip("，,；; ") + "…"
    return out
