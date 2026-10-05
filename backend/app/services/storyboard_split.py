"""长镜拆解：把一镜拆成 2–4 个连续 3–5 秒子镜，保持场景/人物/运镜段落一致。"""
from __future__ import annotations

import json
import math
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Storyboard, StoryboardCharacter
from app.services.llm.client import LLMNotConfigured, chat_text, resolve_llm
from app.services.storyboard_references import resolve_speaking_character_id, sync_storyboard_characters
from app.services.storyboard_segments import assign_segment_fields, refresh_episode_segments


class StoryboardSplitError(Exception):
    pass


_MAX_PARTS = 4
_MIN_PARTS = 2
_SHOT_MAX_SECONDS = 5

# 出图诱导词：拆镜后从动作/提示词剔除
_TEXT_POLLUTION = re.compile(
    r"字幕浮现|字幕出现|字幕升起|字幕|标题卡|花字|水印|"
    r"\bsubtitle[s]?\b|\bcaption[s]?\b|title card|on-?screen text|with text|text overlay",
    re.IGNORECASE,
)


def _dialogue_chars(text: str | None) -> int:
    return len(re.sub(r"[\s，。！？、；：,.!?;:\-—‘’“”\"']", "", text or ""))


def _clean_visual_text(text: str | None) -> str:
    value = _TEXT_POLLUTION.sub(" ", text or "")
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"[，,]{2,}", "，", value)
    return value.strip(" ，,;；")


def suggest_part_count(sb: Storyboard, requested: int | None = None) -> int:
    if requested is not None:
        return max(_MIN_PARTS, min(int(requested), _MAX_PARTS))
    # 约 4 字/秒，5 秒约 20 字；再按动作复杂度兜底
    chars = _dialogue_chars(sb.dialogue)
    by_dialogue = max(1, math.ceil(chars / 18)) if chars else 1
    action_len = len((sb.action or "") + (sb.video_prompt or ""))
    by_action = 3 if action_len > 80 else (2 if action_len > 40 else 1)
    duration = int(sb.duration or 5)
    by_duration = max(1, math.ceil(duration / _SHOT_MAX_SECONDS)) if duration > 5 else 1
    parts = max(by_dialogue, by_action, by_duration, _MIN_PARTS)
    return max(_MIN_PARTS, min(parts, _MAX_PARTS))


def _clone_base_fields(src: Storyboard) -> dict:
    return {
        "episode_id": src.episode_id,
        "scene_id": src.scene_id,
        "location": src.location,
        "time": src.time,
        "angle": src.angle,
        "atmosphere": src.atmosphere,
        "bgm_prompt": src.bgm_prompt,
        "sound_effect": src.sound_effect,
        "speaking_character_id": src.speaking_character_id,
        # 不继承手动锁定参考，拆镜后走智能参考更稳
        "reference_images": None,
    }


def _split_text_chunks(text: str | None, parts: int) -> list[str]:
    raw = (text or "").strip()
    if parts <= 1:
        return [raw]
    if not raw:
        return [""] * parts
    # 优先按句号/分号/逗号切（旁白长句常用逗号）
    pieces = re.split(r"(?<=[。！？；;!?，,])", raw)
    pieces = [p.strip() for p in pieces if p and p.strip()]
    if len(pieces) <= 1:
        n = len(raw)
        size = max(1, math.ceil(n / parts))
        chunks = [raw[i * size : (i + 1) * size].strip() for i in range(parts)]
        while len(chunks) < parts:
            chunks.append("")
        return chunks[:parts]
    # 按句子顺序均分到 parts（保持时间顺序，不用轮转）
    buckets: list[list[str]] = [[] for _ in range(parts)]
    for index, piece in enumerate(pieces):
        # 按比例落入桶，保证从前到后
        bucket = min(parts - 1, int(index * parts / max(len(pieces), 1)))
        buckets[bucket].append(piece)
    result = ["".join(b).strip() for b in buckets]
    for i, item in enumerate(result):
        if not item:
            result[i] = result[i - 1] if i else (result[i + 1] if i + 1 < len(result) else raw)
    while len(result) < parts:
        result.append(result[-1] if result else raw)
    return result[:parts]


def _phase_label(index: int, parts: int) -> str:
    if index == 0:
        return "起幅"
    if index == parts - 1:
        return "落幅"
    if parts == 3:
        return "中段"
    return f"中段{index}"


def _shot_types_for_parts(parts: int, base: str | None) -> list[str]:
    presets = {
        2: ["中景", "近景"],
        3: ["全景", "中景", "近景"],
        4: ["远景", "全景", "中景", "特写"],
    }
    types = list(presets.get(parts, ["中景"] * parts))
    if base and base.strip():
        types[0] = base.strip()
    return types


def _phase_action(phase: str, chunk: str, base_action: str, index: int, parts: int) -> str:
    chunk = _clean_visual_text(chunk) or _clean_visual_text(base_action) or "动作推进"
    if phase == "起幅":
        return f"【起幅】{chunk}；建立场景与人物位置，运镜开始"
    if phase == "落幅":
        return f"【落幅】{chunk}；收束动作与情绪，镜头落稳，不要新增无关人物"
    return f"【{phase}】{chunk}；承接上一镜连续运镜，推进动作"


def _phase_image_prompt(
    sb: Storyboard,
    *,
    phase: str,
    shot_type: str,
    action: str,
    dialogue: str,
    index: int,
    parts: int,
) -> str:
    """为每个子镜生成可区分的画面提示词，避免拆完三镜提示词完全一样。"""
    base = _clean_visual_text(sb.image_prompt)
    location = (sb.location or "").strip()
    time_of_day = (sb.time or "").strip()
    env = _clean_visual_text(", ".join(x for x in (location, time_of_day, sb.atmosphere) if x))
    action_clean = _clean_visual_text(action)
    dialogue_hint = ""
    if dialogue and dialogue.strip():
        # 台词只作情绪/说话状态，明确禁止画面出字
        dialogue_hint = (
            f"人物情绪与台词一致，画面中不要文字字幕（台词仅作表演参考：{dialogue.strip()[:40]}）；"
            "禁止多人并排定装站立"
        )

    # 注意：不要写「全身」——空镜/环境镜会被诱导出人（长安夜色起幅问题）
    framing = {
        "远景": "远景构图，强调环境纵深与空间层次，可不出现人物",
        "全景": "全景构图，交代场景空间与建筑布局，可不出现人物",
        "中景": "中景构图，主体清楚",
        "近景": "近景构图，主体细节清楚",
        "特写": "特写构图，关键道具或细节",
        "中近景": "中近景构图",
    }.get(shot_type or "", "电影感构图")

    phase_hint = {
        "起幅": "运镜起点，建立方位，人物刚进入状态",
        "落幅": "运镜终点，动作收束，画面稳定",
        "中段": "运镜过程中，动作进行时",
    }.get(phase, f"连续运镜第{index + 1}段")

    # 若原提示词太短或与各镜雷同，用结构化中文重写
    pieces = [
        framing,
        phase_hint,
        env or None,
        action_clean or None,
        dialogue_hint or None,
        base if base and index == 0 else None,  # 原提示词作底，避免每镜整段复制
        "历史战争短剧电影感，真实光影，高细节",
        "不要文字、水印、logo、字幕、UI",
    ]
    if index > 0 and base:
        # 后续镜只抽取环境关键词，避免三镜完全同一 prompt
        pieces.insert(2, f"承接同一场景：{location or '连续场景'}")
    return ", ".join(p for p in pieces if p)


def _rule_based_parts(sb: Storyboard, parts: int) -> list[dict]:
    dialogues = _split_text_chunks(sb.dialogue, parts)
    actions = _split_text_chunks(sb.action or sb.description, parts)
    movement = (sb.movement or "运镜").strip() or "运镜"
    shot_types = _shot_types_for_parts(parts, sb.shot_type)
    title_base = re.sub(r"[·・]?\d+/\d+$", "", (sb.title or "镜头").strip()).strip("·・ ") or "镜头"

    results: list[dict] = []
    for i in range(parts):
        phase = _phase_label(i, parts)
        action_chunk = actions[i] if i < len(actions) else (sb.action or "")
        dialogue = dialogues[i] if i < len(dialogues) else ""
        action = _phase_action(phase, action_chunk, sb.action or "", i, parts)
        shot_type = shot_types[i] if i < len(shot_types) else (sb.shot_type or "中景")
        image_prompt = _phase_image_prompt(
            sb, phase=phase, shot_type=shot_type, action=action, dialogue=dialogue or "", index=i, parts=parts,
        )
        results.append(
            {
                "title": f"{title_base}·{phase}",
                "shot_type": shot_type,
                "movement": movement,
                "action": action,
                "dialogue": dialogue or None,
                "result": (
                    f"完成{movement}落幅，画面稳定"
                    if i == parts - 1
                    else f"衔接下一镜，继续{movement}"
                ),
                "image_prompt": image_prompt,
                "video_prompt": (
                    f"0-5秒：{action}。"
                    f"{'承接上一镜连续运镜，' if i > 0 else '从静止起幅开始，'}"
                    f"{'落幅停稳。' if i == parts - 1 else '保持动势未完成。'}"
                    f"{'主要角色口型自然，不要额外路人。' if dialogue else ''}"
                    "不要出现任何字幕或文字，不要多人等大并排定装。"
                ),
                "duration": _SHOT_MAX_SECONDS,
                "description": f"长镜拆解 {i + 1}/{parts}（{phase}）",
            }
        )
    return results


def _normalize_specs(sb: Storyboard, specs: list[dict], parts: int) -> list[dict]:
    """统一 LLM/规则结果：去字幕词、标题分段、提示词差异化。"""
    title_base = re.sub(r"[·・].*$", "", (sb.title or "镜头").strip()).strip("·・ ") or "镜头"
    shot_types = _shot_types_for_parts(parts, sb.shot_type)
    normalized: list[dict] = []
    seen_prompts: set[str] = set()
    for i, item in enumerate(specs[:parts]):
        phase = _phase_label(i, parts)
        shot_type = (item.get("shot_type") or shot_types[i] if i < len(shot_types) else sb.shot_type) or "中景"
        action = _clean_visual_text(item.get("action") or sb.action) or f"【{phase}】动作推进"
        if phase not in action:
            action = f"【{phase}】{action}"
        dialogue = item.get("dialogue")
        if isinstance(dialogue, str):
            dialogue = dialogue.strip() or None
        duration = item.get("duration", 5)
        try:
            duration = max(3, min(int(duration), 5))
        except (TypeError, ValueError):
            duration = 5
        image_prompt = _clean_visual_text(item.get("image_prompt") or "")
        # LLM 常把同一 prompt 复制 N 次 → 强制重写差异化
        if not image_prompt or image_prompt in seen_prompts or image_prompt == _clean_visual_text(sb.image_prompt):
            image_prompt = _phase_image_prompt(
                sb, phase=phase, shot_type=str(shot_type), action=action,
                dialogue=dialogue or "", index=i, parts=parts,
            )
        seen_prompts.add(image_prompt)
        raw_title = str(item.get("title") or "").strip()
        # 禁止标题只剩「起幅/中段/落幅」——否则画布上分不清是哪一段戏
        if not raw_title or raw_title in {phase, f"【{phase}】"} or title_base not in raw_title:
            title = f"{title_base}·{phase}"
        elif phase not in raw_title:
            title = f"{raw_title}·{phase}"
        else:
            title = raw_title
        normalized.append(
            {
                "title": title,
                "shot_type": shot_type,
                "movement": item.get("movement") or sb.movement or "运镜",
                "action": action,
                "dialogue": dialogue,
                "result": item.get("result") or sb.result,
                "image_prompt": image_prompt,
                "video_prompt": _clean_visual_text(item.get("video_prompt") or "")
                or (
                    f"0-5秒：{action}。{'落幅停稳。' if i == parts - 1 else '连续运镜中。'}不要字幕文字。"
                ),
                "duration": duration,
                "description": item.get("description") or f"长镜拆解 {i + 1}/{parts}（{phase}）",
            }
        )
    while len(normalized) < parts:
        normalized.append(normalized[-1])
    return normalized[:parts]


def _llm_parts(db: Session, sb: Storyboard, parts: int) -> list[dict] | None:
    try:
        base_url, api_key, model = resolve_llm(db)
    except LLMNotConfigured:
        return None
    payload = {
        "title": sb.title,
        "location": sb.location,
        "time": sb.time,
        "shot_type": sb.shot_type,
        "angle": sb.angle,
        "movement": sb.movement,
        "action": sb.action,
        "dialogue": sb.dialogue,
        "description": sb.description,
        "result": sb.result,
        "atmosphere": sb.atmosphere,
        "image_prompt": sb.image_prompt,
        "video_prompt": sb.video_prompt,
        "duration": sb.duration,
    }
    prompt = (
        f"把下面这一镜拆成恰好 {parts} 个连续子镜，用于本地 ComfyUI 图生视频（每镜最长 5 秒）。\n"
        "要求：\n"
        "1. 同一场景、同一批人物、同一条运镜轴线，禁止跳切到无关场景；\n"
        "2. 完整覆盖原动作/台词/运镜，从起幅到落幅；\n"
        "3. 台词按语义拆到各子镜，不要每镜重复全文；\n"
        "4. 每镜 duration 为 3–5 的整数；\n"
        "5. video_prompt 只描述本 5 秒内动作，并写明与前后镜的衔接；\n"
        "6. 景别可随推拉摇移递进变化（如 全景→中景→近景）；\n"
        "7. 每镜 title 必须带阶段名：起幅/中段/落幅，互不相同；\n"
        "8. 每镜 image_prompt 必须互不相同，写清本镜景别、人物动作差异、构图差异；禁止三镜复制同一段英文提示词；\n"
        "9. action 与 image_prompt 禁止出现「字幕、花字、水印、屏幕文字」等会画字的词；\n"
        "10. 落幅镜不要无故增加路人。\n"
        '只输出 JSON：{"parts":[{"title":"","shot_type":"","movement":"","action":"","dialogue":"",'
        '"result":"","image_prompt":"","video_prompt":"","duration":5,"description":""}]}\n'
        f"原镜头：{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = chat_text(
            [
                {"role": "system", "content": "你是短剧分镜拆解专家，只输出 JSON。每个子镜必须有差异。"},
                {"role": "user", "content": prompt},
            ],
            base_url,
            api_key,
            model,
            temperature=0.35,
            response_format={"type": "json_object"},
        )
        data = json.loads(raw)
        parts_data = data.get("parts") or []
        if not isinstance(parts_data, list) or len(parts_data) < _MIN_PARTS:
            return None
        cleaned = [item for item in parts_data if isinstance(item, dict)]
        if len(cleaned) < _MIN_PARTS:
            return None
        return _normalize_specs(sb, cleaned, parts)
    except Exception:  # noqa: BLE001
        return None


def _copy_character_links(db: Session, source_id: int, target_id: int) -> None:
    """复制角色关联；跳过已存在，避免与 sync_storyboard_characters 重复插入撞 UNIQUE。"""
    existing = set(
        db.scalars(
            select(StoryboardCharacter.character_id).where(StoryboardCharacter.storyboard_id == target_id)
        ).all()
    )
    # 本会话里尚未 flush 的 pending 也要算上
    for obj in db.new:
        if isinstance(obj, StoryboardCharacter) and obj.storyboard_id == target_id:
            existing.add(obj.character_id)
    links = db.scalars(
        select(StoryboardCharacter).where(StoryboardCharacter.storyboard_id == source_id)
    ).all()
    for link in links:
        if link.character_id in existing:
            continue
        db.add(StoryboardCharacter(storyboard_id=target_id, character_id=link.character_id))
        existing.add(link.character_id)


def _apply_split_voice_fields(db: Session, row: Storyboard) -> None:
    """拆镜后：台词已变，必须清掉旧整段配音，并按本子镜台词重绑说话人。

    旧逻辑只清视频/成图，首镜仍挂父镜完整 TTS，子镜无 TTS → 口型/时长对不上。
    """
    row.tts_audio_url = None
    dialogue = (row.dialogue or "").strip()
    if not dialogue:
        row.speaking_character_id = None
        return
    linked = sync_storyboard_characters(db, row)
    sid = resolve_speaking_character_id(db, row, linked)
    # 台词已拆分：强制按本段重推说话人（保留克隆的仅作回退）
    if sid is not None:
        row.speaking_character_id = sid
    # sid 为空时保留 base 克隆的 speaking_character_id（旁白/单人镜常见）


def split_storyboard(
    db: Session,
    storyboard_id: int,
    *,
    parts: int | None = None,
    use_llm: bool = True,
    commit: bool = True,
) -> dict:
    """长镜拆解。commit=False 时供审核整改批处理，由调用方统一提交。"""
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise StoryboardSplitError("分镜不存在")

    part_count = suggest_part_count(sb, parts)
    specs = _llm_parts(db, sb, part_count) if use_llm else None
    if not specs:
        specs = _normalize_specs(sb, _rule_based_parts(sb, part_count), part_count)
    else:
        specs = _normalize_specs(sb, specs, part_count)
    part_count = len(specs)

    segment_key = sb.segment_key or f"seg-{uuid.uuid4().hex[:10]}"
    segment_title = (
        sb.segment_title
        or f"{(sb.movement or '运镜').strip() or '连续动作'}·{(sb.title or '长镜').strip()}"
    )
    # 去掉旧标题里的 ·起幅 等，避免叠字
    segment_title = re.sub(r"[·・](起幅|中段\d*|落幅).*$", "", segment_title).strip("·・ ") or segment_title

    # 后面的镜号整体后移
    followers = db.scalars(
        select(Storyboard)
        .where(
            Storyboard.episode_id == sb.episode_id,
            Storyboard.deleted_at.is_(None),
            Storyboard.storyboard_number > sb.storyboard_number,
        )
        .order_by(Storyboard.storyboard_number.desc())
    ).all()
    shift = part_count - 1
    for row in followers:
        row.storyboard_number = int(row.storyboard_number) + shift

    base_number = int(sb.storyboard_number)
    base_fields = _clone_base_fields(sb)
    # 原成图作为「段落环境锚点」写入每镜 first_frame，保证拆镜后环境一致、视频可上下衔接
    anchor_image = sb.composed_image or sb.first_frame_image
    env_hint = (
        f"与本段落其他子镜同一环境（{sb.location or '原场景'}），"
        "帐篷/营火/建筑/光线布局保持一致，只改变景别与人物动作"
        if (sb.location or anchor_image)
        else "与本段落其他子镜保持同一环境与光线"
    )
    created: list[Storyboard] = []

    # 第 1 部分覆写原镜
    first = specs[0]
    sb.title = first.get("title") or sb.title
    sb.shot_type = first.get("shot_type") or sb.shot_type
    sb.movement = first.get("movement") or sb.movement
    sb.action = first.get("action") or sb.action
    sb.dialogue = first.get("dialogue")
    sb.result = first.get("result") or sb.result
    # 提示词末尾强制环境一致
    ip = (first.get("image_prompt") or sb.image_prompt or "").strip()
    sb.image_prompt = f"{ip}，{env_hint}" if ip and env_hint not in ip else (ip or env_hint)
    sb.video_prompt = first.get("video_prompt") or sb.video_prompt
    sb.description = first.get("description") or sb.description
    sb.duration = max(3, min(int(first.get("duration") or 5), 5))
    sb.storyboard_number = base_number
    sb.reference_images = None
    # 清空成图以强制按新提示词重出；保留 first_frame=原图作环境锚点
    sb.video_url = None
    sb.composed_video_url = None
    sb.last_frame_image = None
    sb.composed_image = None
    sb.first_frame_image = anchor_image
    # 台词已拆到本子镜：必须清掉父镜整段配音，否则对口型/时长错位
    sb.tts_audio_url = None
    sb.status = "pending"
    created.append(sb)

    for offset, spec in enumerate(specs[1:], start=1):
        ip = (spec.get("image_prompt") or "").strip()
        ip = f"{ip}，{env_hint}" if ip and env_hint not in ip else (ip or env_hint)
        row = Storyboard(
            **base_fields,
            storyboard_number=base_number + offset,
            title=spec.get("title"),
            shot_type=spec.get("shot_type"),
            movement=spec.get("movement"),
            action=spec.get("action"),
            dialogue=spec.get("dialogue"),
            result=spec.get("result"),
            image_prompt=ip,
            video_prompt=spec.get("video_prompt"),
            description=spec.get("description"),
            duration=max(3, min(int(spec.get("duration") or 5), 5)),
            # 不复制成图（避免三镜同一张）；first_frame=段落环境底图
            composed_image=None,
            first_frame_image=anchor_image,
            # 不继承父镜整段 TTS
            tts_audio_url=None,
            status="pending",
        )
        db.add(row)
        db.flush()
        _copy_character_links(db, sb.id, row.id)
        created.append(row)

    # 每个子镜：按本段台词重绑说话人 + 确保无旧 TTS
    for row in created:
        _apply_split_voice_fields(db, row)

    assign_segment_fields(created, key=segment_key, title=segment_title)
    refresh_episode_segments(db, sb.episode_id, commit=False)
    if commit:
        db.commit()
        for row in created:
            db.refresh(row)
    else:
        db.flush()

    needs_tts_ids = [row.id for row in created if (row.dialogue or "").strip()]
    return {
        "source_storyboard_id": storyboard_id,
        "segment_key": segment_key,
        "segment_title": segment_title,
        "parts": part_count,
        "storyboard_ids": [row.id for row in created],
        "storyboard_numbers": [row.storyboard_number for row in created],
        "needs_reimage": True,
        "needs_tts": bool(needs_tts_ids),
        "needs_tts_ids": needs_tts_ids,
        "message": (
            "已拆成连续子镜；图与旧配音已清空，台词已按子镜拆分。"
            "请批量出图；有台词的子镜将自动重配本段语音。"
            if needs_tts_ids
            else "已拆成连续子镜；图已清空，请按新提示词批量出图"
        ),
    }
