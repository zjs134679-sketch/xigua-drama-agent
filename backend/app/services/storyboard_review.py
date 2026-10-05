"""分镜监督 Agent：确定性规则 + LLM 语义审核 + 版本化报告 + 可验证整改。"""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, Episode, Prop, Scene, Storyboard, StoryboardCharacter, StoryboardReview
from app.services.llm.client import chat_text, resolve_llm


class StoryboardReviewError(Exception):
    pass


SEVERITY_ORDER = {"severe": 0, "medium": 1, "minor": 2}
SEVERITY_ALIASES = {
    "severe": "severe", "严重": "severe", "red": "severe",
    "medium": "medium", "中等": "medium", "yellow": "medium",
    "minor": "minor", "轻微": "minor", "white": "minor",
}

# 景别循环：用于打散连续三镜同景别
_SHOT_CYCLE = ("近景", "中景", "全景", "特写", "中近景")
_DEFAULT_ANGLE = "平视"
_DEFAULT_MOVEMENT = "固定"
_DEFAULT_ACTION = "人物保持可拍动作，表情与情绪外化"

EDITABLE_FIELDS = {
    "title", "location", "time", "shot_type", "angle", "movement", "action", "result", "atmosphere",
    "image_prompt", "video_prompt", "bgm_prompt", "sound_effect", "dialogue", "description", "duration",
}


def _text(value: str | None, limit: int = 4000) -> str:
    return (value or "").strip()[:limit]


def _issue(severity: str, category: str, problem: str, suggestion: str, shots: list[int] | None = None) -> dict:
    return {
        "severity": severity,
        "category": category,
        "storyboard_numbers": shots or [],
        "problem": problem.strip(),
        "suggestion": suggestion.strip(),
    }


def _normalize_script_text(value: str | None) -> str:
    """去掉空白/标点/说话人前缀，用于台词宽松比对。"""
    text = (value or "").strip()
    text = re.sub(r"^[\u4e00-\u9fffA-Za-z0-9_·]{1,16}\s*[:：]\s*", "", text)
    text = re.sub(r"[\s\u3000，。！？、；：,.!?;:\-—…‘’“”\"'（）()【】\[\]《》<>]", "", text)
    return text.lower()


def dialogue_in_script(dialogue: str | None, script: str | None) -> bool:
    """台词是否被剧本覆盖：先精确包含，再规范化包含。"""
    raw_d = _text(dialogue)
    raw_s = script or ""
    if not raw_d:
        return True
    if raw_d in raw_s:
        return True
    norm_d = _normalize_script_text(raw_d)
    norm_s = _normalize_script_text(raw_s)
    if not norm_d:
        return True
    if len(norm_d) >= 4 and norm_d in norm_s:
        return True
    # 短台词：允许作为规范化剧本的子串或高度重叠
    if len(norm_d) < 4 and (norm_d in norm_s or any(norm_d in chunk for chunk in re.findall(r".{1,20}", norm_s))):
        return True
    return False


def scene_location_matched(location: str | None, scene_names: set[str]) -> bool:
    """场景名匹配：精确 → 互为包含（去掉空白）。"""
    loc = (location or "").strip()
    if not loc or not scene_names:
        return True
    if loc in scene_names:
        return True
    compact = re.sub(r"\s+", "", loc)
    for name in scene_names:
        n = re.sub(r"\s+", "", name)
        if not n:
            continue
        if compact == n or compact in n or n in compact:
            return True
    return False


def best_scene_location(location: str | None, scene_names: set[str]) -> str | None:
    """把镜头 location 纠正到最近的场景资产名。"""
    loc = (location or "").strip()
    if not loc or not scene_names:
        return None
    if loc in scene_names:
        return None
    compact = re.sub(r"\s+", "", loc)
    # 优先：资产名被 location 包含 / location 被资产名包含
    for name in sorted(scene_names, key=len, reverse=True):
        n = re.sub(r"\s+", "", name)
        if n and (n in compact or compact in n):
            return name
    # 次选：共享最长公共子串（>=2）
    best_name = None
    best_score = 0
    for name in scene_names:
        n = re.sub(r"\s+", "", name)
        score = 0
        for i in range(len(compact)):
            for j in range(i + 2, len(compact) + 1):
                if compact[i:j] in n:
                    score = max(score, j - i)
        if score > best_score:
            best_score = score
            best_name = name
    return best_name if best_score >= 2 else None


def style_prompt_issues(
    storyboards: list[Storyboard],
    *,
    must_include: list[str] | None = None,
    must_exclude: list[str] | None = None,
) -> list[dict]:
    """风格一致性：锚词缺失 / 禁用词命中（西瓜原创规则）。"""
    issues: list[dict] = []
    includes = [w for w in (must_include or []) if w and len(w) >= 2][:8]
    excludes = [w for w in (must_exclude or []) if w and len(w) >= 2][:12]
    if not includes and not excludes:
        return issues
    for sb in storyboards:
        blob = " ".join(
            x for x in (sb.image_prompt, sb.video_prompt, sb.atmosphere, sb.action) if x
        )
        if not blob.strip():
            continue
        number = sb.storyboard_number
        if includes:
            missing = [w for w in includes if w not in blob]
            # 锚词较多时：缺失超过一半才报，避免误伤
            if missing and len(missing) >= max(2, len(includes) // 2):
                issues.append(_issue(
                    "minor",
                    "风格锚词",
                    f"镜头 {number} 画面提示词缺少画风锚词：{'、'.join(missing[:5])}。",
                    "在 image_prompt 中补入项目画风必带锚词（见画风约束）。",
                    [number],
                ))
        if excludes:
            hits = [w for w in excludes if w in blob]
            if hits:
                issues.append(_issue(
                    "medium",
                    "风格禁用",
                    f"镜头 {number} 提示词命中风格禁用：{'、'.join(hits[:5])}。",
                    "删除或改写禁用描述，保持画风统一。",
                    [number],
                ))
    return issues


def structural_issues(storyboards: list[Storyboard], script: str, scenes: list[Scene]) -> list[dict]:
    """不依赖 LLM 的硬规则检查，也作为 Agent 的事实输入。"""
    issues: list[dict] = []
    scene_names = {row.location.strip() for row in scenes if row.location and row.deleted_at is None}
    for sb in storyboards:
        number = sb.storyboard_number
        missing = [label for label, value in (
            ("动作", sb.action), ("景别", sb.shot_type), ("角度", sb.angle), ("运镜", sb.movement)
        ) if not _text(value)]
        if missing:
            issues.append(_issue("medium", "字段完整性", f"镜头 {number} 缺少{'、'.join(missing)}。", "补齐可直接执行的镜头字段。", [number]))
        if sb.duration <= 0:
            issues.append(_issue("severe", "片段时长", f"镜头 {number} 时长无效。", "设置 3–5 秒的时长。", [number]))
        elif sb.duration > 5:
            issues.append(_issue(
                "severe", "片段时长",
                f"镜头 {number} 时长 {sb.duration} 秒，超过 ComfyUI 单镜上限 5 秒。",
                "按动作或台词语义拆成连续镜头，每镜 3–5 秒。（一键整改只能压到 5 秒，无法自动拆镜）",
                [number],
            ))

        # 台词写在 video_prompt 却没进 dialogue → 成片会闭嘴/乱说
        from app.services.video_generation import extract_dialogue_from_shot_fields

        harvested = extract_dialogue_from_shot_fields(
            sb.dialogue, sb.video_prompt, sb.action, sb.image_prompt
        )
        if harvested and not _text(sb.dialogue):
            issues.append(_issue(
                "severe",
                "台词字段",
                f"镜头 {number} 的对白写在提示词/动作里，但 dialogue 为空。",
                "把「角色名：台词」挪到 dialogue 字段，供成片照念生成语音。",
                [number],
            ))

        dialogue = _text(sb.dialogue) or harvested
        dialogue_chars = len(_normalize_script_text(dialogue)) if dialogue else 0
        # 动作写「说话/开口」却无台词
        action_l = _text(sb.action)
        if not dialogue and any(k in action_l for k in ("说话", "开口", "说道", "喊道", "低语", "对白")):
            issues.append(_issue(
                "medium",
                "台词缺失",
                f"镜头 {number} 动作含说话，但 dialogue 为空。",
                "补上「角色名：台词」原文，或改成无对白动作描述。",
                [number],
            ))
        if dialogue_chars:
            minimum = math.ceil(dialogue_chars / 4 + 1)
            if minimum > 5:
                issues.append(_issue(
                    "medium", "台词时长",
                    f"镜头 {number} 有约 {dialogue_chars} 字台词，按语速约需 {minimum} 秒，超过单镜 5 秒上限。",
                    "按语义拆成多个连续镜头，每镜台词控制在约 20 字以内。（一键整改无法新增镜头）",
                    [number],
                ))
            elif sb.duration < minimum:
                issues.append(_issue(
                    "medium", "台词时长",
                    f"镜头 {number} 有约 {dialogue_chars} 字台词，但时长仅 {sb.duration} 秒。",
                    f"将时长提高到至少 {minimum} 秒（不超过 5），或按语义拆镜。",
                    [number],
                ))
            if script and not dialogue_in_script(dialogue, script):
                issues.append(_issue(
                    "severe", "台词完整性",
                    f"镜头 {number} 的台词未在剧本中找到对应（允许标点/说话人前缀差异）。",
                    "恢复剧本原文并核对说话人，不要改写或概括。",
                    [number],
                ))
        elif sb.duration > 5:
            issues.append(_issue(
                "medium", "片段时长",
                f"镜头 {number} 无台词但持续 {sb.duration} 秒。",
                "压缩到 5 秒以内，或补充持续发生的动作/运镜变化。",
                [number],
            ))

        if scene_names and _text(sb.location) and not scene_location_matched(sb.location, scene_names):
            issues.append(_issue(
                "severe", "场景资产关联",
                f"镜头 {number} 的场景“{sb.location}”未匹配已有场景资产。",
                "改用已有场景名称，或先补齐并关联对应场景资产。",
                [number],
            ))

    for index in range(len(storyboards) - 2):
        group = storyboards[index:index + 3]
        shot_types = [_text(sb.shot_type).lower() for sb in group]
        if shot_types[0] and len(set(shot_types)) == 1:
            numbers = [sb.storyboard_number for sb in group]
            issues.append(_issue(
                "minor", "景别视角错开",
                f"镜头 {numbers[0]}–{numbers[-1]} 连续使用相同景别“{group[0].shot_type}”。",
                "至少调整其中一镜的景别或视角，形成视觉节奏。",
                numbers,
            ))
    return issues


def _context(db: Session, episode: Episode, storyboards: list[Storyboard]) -> tuple[dict, list[Scene]]:
    characters = db.scalars(select(Character).where(Character.drama_id == episode.drama_id, Character.deleted_at.is_(None)).order_by(Character.id)).all()
    scenes = db.scalars(select(Scene).where(Scene.drama_id == episode.drama_id, Scene.deleted_at.is_(None)).order_by(Scene.id)).all()
    props = db.scalars(select(Prop).where(Prop.drama_id == episode.drama_id, Prop.deleted_at.is_(None)).order_by(Prop.id)).all()
    links = db.execute(
        select(StoryboardCharacter.storyboard_id, StoryboardCharacter.character_id)
        .where(StoryboardCharacter.storyboard_id.in_([sb.id for sb in storyboards]))
    ).all() if storyboards else []
    linked: dict[int, list[int]] = {}
    for storyboard_id, character_id in links:
        linked.setdefault(storyboard_id, []).append(character_id)
    context = {
        "episode": {"id": episode.id, "title": episode.title, "script": _text(episode.script_content, 24000)},
        "assets": {
            "characters": [{"id": x.id, "name": x.name, "appearance": _text(x.appearance, 500)} for x in characters],
            "scenes": [{"id": x.id, "location": x.location, "time": x.time} for x in scenes],
            "props": [{"id": x.id, "name": x.name, "description": _text(x.description, 500)} for x in props],
        },
        "storyboards": [{
            "id": sb.id, "number": sb.storyboard_number, "title": sb.title, "location": sb.location,
            "time": sb.time, "shot_type": sb.shot_type, "angle": sb.angle, "movement": sb.movement,
            "action": sb.action, "dialogue": sb.dialogue, "sound_effect": sb.sound_effect,
            "duration": sb.duration, "image_prompt": sb.image_prompt, "video_prompt": sb.video_prompt,
            "linked_character_ids": linked.get(sb.id, []),
        } for sb in storyboards],
    }
    return context, scenes


def _json_object(raw: str) -> dict:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise StoryboardReviewError("审核 Agent 返回了无法解析的结果") from exc
        data = json.loads(value[start:end + 1])
    if not isinstance(data, dict):
        raise StoryboardReviewError("审核 Agent 返回格式不是对象")
    return data


def normalize_llm_issues(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        severity = SEVERITY_ALIASES.get(str(item.get("severity", "")).lower())
        problem = _text(str(item.get("problem") or ""), 1200)
        if not severity or not problem:
            continue
        numbers: list[int] = []
        if isinstance(item.get("storyboard_numbers"), list):
            for number in item["storyboard_numbers"]:
                try:
                    numbers.append(int(number))
                except (TypeError, ValueError):
                    pass
        result.append(_issue(
            severity,
            _text(str(item.get("category") or "综合审核"), 80),
            problem,
            _text(str(item.get("suggestion") or "请按问题描述修订。"), 1200),
            numbers,
        ))
    return result


def merge_issues(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group:
            key = (item["category"], item["problem"])
            if key not in seen:
                seen.add(key)
                merged.append(item)
    merged.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], item["storyboard_numbers"] or [999999]))
    for index, item in enumerate(merged, start=1):
        item["id"] = index
    return merged


def grade_issues(issues: list[dict]) -> tuple[str, dict[str, int]]:
    counts = {key: sum(1 for item in issues if item["severity"] == key) for key in SEVERITY_ORDER}
    if counts["severe"] >= 3:
        grade = "D"
    elif counts["severe"] >= 1 or counts["medium"] > 5:
        grade = "C"
    elif counts["medium"] > 2:
        grade = "B"
    else:
        grade = "A"
    return grade, counts


def review_view(row: StoryboardReview) -> dict:
    report = json.loads(row.report_json)
    report.update({
        "id": row.id, "episode_id": row.episode_id, "grade": row.grade, "summary": row.summary,
        "counts": {"severe": row.severe_count, "medium": row.medium_count, "minor": row.minor_count},
        "model": row.model, "instruction": row.instruction,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })
    return report


def run_storyboard_review(db: Session, episode_id: int, instruction: str | None = None) -> dict:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.deleted_at is not None:
        raise LookupError("分集不存在")
    storyboards = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    if not storyboards:
        raise StoryboardReviewError("该分集还没有分镜，请先生成分镜")
    if not _text(episode.script_content):
        raise StoryboardReviewError("该分集还没有剧本，无法核对台词与剧情覆盖")

    context, scenes = _context(db, episode, storyboards)
    structural = structural_issues(storyboards, context["episode"]["script"], scenes)
    try:
        from app.models.domain import Drama
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_AUDIT

        drama = db.get(Drama, episode.drama_id)
        contract = compose(db=db, drama=drama, stage=STAGE_AUDIT)
        structural.extend(
            style_prompt_issues(
                storyboards,
                must_include=contract.must_include,
                must_exclude=contract.must_exclude,
            )
        )
    except Exception:  # noqa: BLE001
        pass
    base_url, api_key, model = resolve_llm(db)
    skill_path = Path(__file__).resolve().parent / "agents" / "skills" / "xg_shot_audit" / "SKILL.md"
    prompt = (
        "审核下列整集数据。确定性检查已发现的问题也提供给你，请继续检查语义层面的遗漏，尤其是剧本台词覆盖、"
        "人物不消失、VO 音画同步、资产关联、人物外观混入提示词和镜头连贯性。\n"
        "不要重复罗列确定性检查里已经写明的问题；只补充语义层面遗漏。\n"
        "输出 JSON：{\"summary\":\"一句话总评\",\"issues\":[{\"severity\":\"severe|medium|minor\","
        "\"category\":\"审核项\",\"storyboard_numbers\":[1],\"problem\":\"具体问题\",\"suggestion\":\"可执行建议\"}],"
        "\"decisions\":[\"仅在严重问题有多种方案时填写用户需决定的问题\"]}。\n"
        f"补充审核要求：{_text(instruction, 1000) or '无'}\n"
        f"确定性检查：{json.dumps(structural, ensure_ascii=False)}\n"
        f"项目数据：{json.dumps(context, ensure_ascii=False)}"
    )
    raw = chat_text(
        [{"role": "system", "content": skill_path.read_text(encoding="utf-8")}, {"role": "user", "content": prompt}],
        base_url, api_key, model, temperature=0.2, timeout=180.0, response_format={"type": "json_object"},
    )
    llm_data = _json_object(raw)
    issues = merge_issues(structural, normalize_llm_issues(llm_data.get("issues")))
    grade, counts = grade_issues(issues)
    fallback = f"共发现 {len(issues)} 个问题：严重 {counts['severe']}、中等 {counts['medium']}、轻微 {counts['minor']}。"
    summary = _text(str(llm_data.get("summary") or fallback), 800)
    raw_decisions = llm_data.get("decisions") if isinstance(llm_data.get("decisions"), list) else []
    report = {"issues": issues, "decisions": [_text(str(x), 500) for x in raw_decisions if _text(str(x), 500)]}
    row = StoryboardReview(
        episode_id=episode_id, grade=grade, summary=summary,
        severe_count=counts["severe"], medium_count=counts["medium"], minor_count=counts["minor"],
        report_json=json.dumps(report, ensure_ascii=False), model=model, instruction=_text(instruction, 1000) or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return review_view(row)


def latest_storyboard_review(db: Session, episode_id: int) -> dict | None:
    row = db.scalars(
        select(StoryboardReview).where(StoryboardReview.episode_id == episode_id).order_by(StoryboardReview.id.desc())
    ).first()
    return review_view(row) if row else None


def list_storyboard_reviews(db: Session, episode_id: int) -> list[dict]:
    rows = db.scalars(
        select(StoryboardReview).where(StoryboardReview.episode_id == episode_id).order_by(StoryboardReview.id.desc())
    ).all()
    return [review_view(row) for row in rows]


def _apply_field_change(sb: Storyboard, field: str, value: object) -> tuple[object, object] | None:
    """应用单个字段修改；成功返回 (before, after)，跳过则 None。"""
    if field not in EDITABLE_FIELDS:
        return None
    if field == "duration":
        try:
            clean_value: object = max(1, min(5, int(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    elif isinstance(value, str):
        clean_value = value.strip()[:8000]
        if not clean_value and field not in {"dialogue", "sound_effect", "bgm_prompt", "description"}:
            return None
    else:
        return None
    old_value = getattr(sb, field)
    if old_value == clean_value:
        return None
    setattr(sb, field, clean_value)
    return old_value, clean_value


def _dialogue_min_seconds(dialogue: str | None) -> int:
    """按约 4 字/秒估算台词所需秒数（含半拍），未超长时返回 0–5。"""
    text = _text(dialogue)
    if not text:
        return 0
    chars = len(_normalize_script_text(text))
    if not chars:
        return 0
    return math.ceil(chars / 4 + 1)


def needs_auto_split(sb: Storyboard) -> bool:
    """是否必须拆镜才能过生产硬规则（单镜≤5s + 台词节奏）。"""
    if int(sb.duration or 0) > 5:
        return True
    return _dialogue_min_seconds(sb.dialogue) > 5


def collect_auto_split_ids(storyboards: list[Storyboard]) -> list[int]:
    """收集需自动拆镜的分镜 id（按镜号从大到小，减少编号偏移干扰）。"""
    candidates = [sb for sb in storyboards if sb.id is not None and needs_auto_split(sb)]
    candidates.sort(key=lambda row: int(row.storyboard_number or 0), reverse=True)
    return [int(row.id) for row in candidates]


def apply_auto_splits(
    db: Session,
    storyboard_ids: list[int],
    *,
    use_llm: bool = False,
) -> list[dict]:
    """对超长/长台词镜头调用 split_storyboard（默认规则拆，稳定可测）。"""
    from app.services.storyboard_split import StoryboardSplitError, split_storyboard

    audit: list[dict] = []
    for sid in storyboard_ids:
        sb = db.get(Storyboard, sid)
        if sb is None or sb.deleted_at is not None:
            continue
        if not needs_auto_split(sb):
            continue
        before_no = int(sb.storyboard_number or 0)
        before_dialogue = _text(sb.dialogue, 200)
        before_duration = int(sb.duration or 0)
        try:
            result = split_storyboard(db, sid, use_llm=use_llm, commit=False)
        except StoryboardSplitError as exc:
            audit.append({
                "storyboard_id": sid,
                "storyboard_number": before_no,
                "source": "auto_split",
                "before": {"duration": before_duration, "dialogue": before_dialogue},
                "after": {"error": str(exc)},
            })
            continue
        parts = int(result.get("parts") or 0)
        numbers = result.get("storyboard_numbers") or []
        audit.append({
            "storyboard_id": sid,
            "storyboard_number": before_no,
            "source": "auto_split",
            "before": {
                "duration": before_duration,
                "dialogue": before_dialogue,
                "storyboard_number": before_no,
            },
            "after": {
                "split_parts": parts,
                "storyboard_ids": result.get("storyboard_ids") or [],
                "storyboard_numbers": numbers,
                "needs_reimage": True,
                "needs_tts": bool(result.get("needs_tts")),
            },
        })
    return audit


def apply_style_prompt_fixes(
    storyboards: list[Storyboard],
    *,
    must_include: list[str] | None = None,
    must_exclude: list[str] | None = None,
) -> list[dict]:
    """确定性补画风锚词、剔除禁用词。"""
    includes = [w for w in (must_include or []) if w and len(w) >= 2][:8]
    excludes = [w for w in (must_exclude or []) if w and len(w) >= 2][:12]
    if not includes and not excludes:
        return []
    audit: list[dict] = []
    for sb in storyboards:
        before: dict[str, object] = {}
        after: dict[str, object] = {}
        prompt = _text(sb.image_prompt)
        original = prompt
        if excludes and prompt:
            for word in excludes:
                if word in prompt:
                    prompt = prompt.replace(word, "")
            prompt = re.sub(r"[，,]{2,}", "，", prompt)
            prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ，,")
        if includes:
            missing = [w for w in includes if w not in prompt]
            if missing:
                prompt = (prompt + "，" if prompt else "") + "，".join(missing)
        if prompt != original:
            pair = _apply_field_change(sb, "image_prompt", prompt or original)
            if pair:
                before["image_prompt"], after["image_prompt"] = pair
        # 视频提示词同样剔禁用词
        vprompt = _text(sb.video_prompt)
        if excludes and vprompt:
            v2 = vprompt
            for word in excludes:
                if word in v2:
                    v2 = v2.replace(word, "")
            v2 = re.sub(r"[，,]{2,}", "，", v2)
            v2 = re.sub(r"\s{2,}", " ", v2).strip(" ，,")
            if v2 != vprompt:
                pair = _apply_field_change(sb, "video_prompt", v2)
                if pair:
                    before["video_prompt"], after["video_prompt"] = pair
        if after:
            audit.append({
                "storyboard_id": sb.id,
                "storyboard_number": sb.storyboard_number,
                "source": "deterministic",
                "before": before,
                "after": after,
            })
    return audit


def apply_prompt_scaffold_fixes(storyboards: list[Storyboard]) -> list[dict]:
    """缺 image/video_prompt 时用动作/景别脚手架补齐，避免空提示词无法出图。"""
    audit: list[dict] = []
    for sb in storyboards:
        before: dict[str, object] = {}
        after: dict[str, object] = {}
        if not _text(sb.image_prompt):
            bits = [
                _text(sb.shot_type) or "中景",
                _text(sb.location),
                _text(sb.time),
                _text(sb.action) or _DEFAULT_ACTION,
                _text(sb.atmosphere),
                "电影感光影，高细节，不要文字水印字幕",
            ]
            scaffold = "，".join(b for b in bits if b)
            pair = _apply_field_change(sb, "image_prompt", scaffold)
            if pair:
                before["image_prompt"], after["image_prompt"] = pair
        if not _text(sb.video_prompt):
            action = _text(sb.action) or _DEFAULT_ACTION
            dur = max(3, min(int(sb.duration or 5), 5))
            scaffold = f"0-{dur}秒：{action}。镜头稳定，人物动作自然，不要字幕文字。"
            pair = _apply_field_change(sb, "video_prompt", scaffold)
            if pair:
                before["video_prompt"], after["video_prompt"] = pair
        if after:
            audit.append({
                "storyboard_id": sb.id,
                "storyboard_number": sb.storyboard_number,
                "source": "deterministic",
                "before": before,
                "after": after,
            })
    return audit


def apply_character_link_fixes(db: Session, storyboards: list[Storyboard]) -> list[dict]:
    """同步角色关联 + 按台词重推说话人（有台词却未绑说话人时）。"""
    from app.services.storyboard_references import resolve_speaking_character_id, sync_storyboard_characters
    from app.services.video_generation import (
        extract_dialogue_from_shot_fields,
        infer_speaking_character_id,
    )

    audit: list[dict] = []
    for sb in storyboards:
        before_sid = sb.speaking_character_id
        before_dialogue = _text(sb.dialogue)
        try:
            # 先收回台词再绑说话人
            harvested = extract_dialogue_from_shot_fields(
                sb.dialogue, sb.video_prompt, sb.action, sb.image_prompt
            )
            if harvested and not before_dialogue:
                sb.dialogue = harvested
            linked = sync_storyboard_characters(db, sb)
            if (sb.dialogue or "").strip():
                sid = (
                    resolve_speaking_character_id(db, sb, linked)
                    or infer_speaking_character_id(db, sb)
                )
                if sid is not None and sid != sb.speaking_character_id:
                    sb.speaking_character_id = sid
        except Exception:  # noqa: BLE001
            continue
        changed: dict[str, object] = {}
        before: dict[str, object] = {}
        if sb.speaking_character_id != before_sid:
            before["speaking_character_id"] = before_sid
            changed["speaking_character_id"] = sb.speaking_character_id
        if _text(sb.dialogue) != before_dialogue:
            before["dialogue"] = before_dialogue
            changed["dialogue"] = _text(sb.dialogue, 200)
        if changed:
            audit.append({
                "storyboard_id": sb.id,
                "storyboard_number": sb.storyboard_number,
                "source": "deterministic",
                "before": before,
                "after": changed,
            })
    return audit


def apply_deterministic_fixes(
    storyboards: list[Storyboard],
    script: str,
    scenes: list[Scene],
) -> list[dict]:
    """
    不依赖 LLM 的硬修复：时长钳制、补字段、场景名对齐、台词时长、景别打散、
    提示词脚手架。超长台词/时长由 apply_auto_splits 先行拆镜。
    """
    scene_names = {row.location.strip() for row in scenes if row.location and row.deleted_at is None}
    audit: list[dict] = []

    def record(sb: Storyboard, before: dict, after: dict) -> None:
        if after:
            audit.append({
                "storyboard_id": sb.id,
                "storyboard_number": sb.storyboard_number,
                "source": "deterministic",
                "before": before,
                "after": after,
            })

    from app.services.video_generation import extract_dialogue_from_shot_fields

    for sb in storyboards:
        before: dict[str, object] = {}
        after: dict[str, object] = {}

        # 把散落在 video_prompt/action 的「角色：台词」收回 dialogue
        harvested = extract_dialogue_from_shot_fields(
            sb.dialogue, sb.video_prompt, sb.action, sb.image_prompt
        )
        if harvested and not _text(sb.dialogue):
            pair = _apply_field_change(sb, "dialogue", harvested)
            if pair:
                before["dialogue"], after["dialogue"] = pair

        # 时长（已拆镜后仍 >5 的兜底钳制）
        if sb.duration <= 0:
            pair = _apply_field_change(sb, "duration", 4)
            if pair:
                before["duration"], after["duration"] = pair
        elif sb.duration > 5:
            pair = _apply_field_change(sb, "duration", 5)
            if pair:
                before["duration"], after["duration"] = pair

        # 缺字段
        defaults = {
            "action": _DEFAULT_ACTION,
            "shot_type": "中景",
            "angle": _DEFAULT_ANGLE,
            "movement": _DEFAULT_MOVEMENT,
        }
        for field, default in defaults.items():
            if not _text(getattr(sb, field, None)):
                pair = _apply_field_change(sb, field, default)
                if pair:
                    before[field], after[field] = pair

        # 场景名对齐：非资产名原文时尽量纠正（含「城门口→城门」这类包含匹配）
        if scene_names and _text(sb.location) and sb.location.strip() not in scene_names:
            fixed = best_scene_location(sb.location, scene_names)
            if fixed and fixed != sb.location.strip():
                pair = _apply_field_change(sb, "location", fixed)
                if pair:
                    before["location"], after["location"] = pair

        # 台词偏短 → 拉高时长（不超过 5）；仍超 5 秒语速需求的已在 auto_split
        dialogue = _text(sb.dialogue)
        if dialogue:
            dialogue_chars = len(_normalize_script_text(dialogue))
            if dialogue_chars:
                minimum = min(5, math.ceil(dialogue_chars / 4 + 1))
                if sb.duration < minimum <= 5:
                    pair = _apply_field_change(sb, "duration", minimum)
                    if pair:
                        before["duration"], after["duration"] = pair

        record(sb, before, after)

    # 连续三镜同景别：改中间一镜
    for index in range(len(storyboards) - 2):
        group = storyboards[index:index + 3]
        types = [_text(sb.shot_type) for sb in group]
        if types[0] and len(set(types)) == 1:
            mid = group[1]
            current = _text(mid.shot_type)
            nxt = next((s for s in _SHOT_CYCLE if s != current), "特写")
            pair = _apply_field_change(mid, "shot_type", nxt)
            if pair:
                record(mid, {"shot_type": pair[0]}, {"shot_type": pair[1]})

    audit.extend(apply_prompt_scaffold_fixes(storyboards))
    return audit


def _merge_audit_changes(groups: list[list[dict]]) -> list[dict]:
    """按 storyboard_number 合并多次修改的 before/after。"""
    by_number: dict[int, dict] = {}
    for group in groups:
        for item in group:
            number = int(item["storyboard_number"])
            slot = by_number.setdefault(number, {
                "storyboard_id": item.get("storyboard_id"),
                "storyboard_number": number,
                "source": item.get("source", "llm"),
                "before": {},
                "after": {},
            })
            # before 只保留首次
            for k, v in (item.get("before") or {}).items():
                slot["before"].setdefault(k, v)
            slot["after"].update(item.get("after") or {})
            if item.get("source") == "deterministic" and slot["source"] == "llm":
                slot["source"] = "mixed"
            elif item.get("source") == "llm" and slot["source"] == "deterministic":
                slot["source"] = "mixed"
            elif item.get("source") and slot["source"] not in ("mixed", item.get("source")):
                slot["source"] = "mixed"
    return [by_number[k] for k in sorted(by_number)]


def _reload_episode_storyboards(db: Session, episode_id: int) -> list[Storyboard]:
    return list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number)
        ).all()
    )


def remediate_storyboard_review(
    db: Session,
    review_id: int,
    instruction: str | None = None,
) -> dict:
    """
    依据审核报告自动整改：
    1) 超长/长台词 → 自动拆镜（规则拆，可增镜头）
    2) 确定性硬修复（时长/字段/场景/景别/提示词脚手架）
    3) 画风锚词/禁用词、角色说话人关联
    4) LLM 语义改字段（台词对齐剧本、动作可拍等）
    5) 改后硬规则重验，回写剩余问题与新评级
    """
    row = db.get(StoryboardReview, review_id)
    if row is None:
        raise LookupError("审核报告不存在")
    episode = db.get(Episode, row.episode_id)
    if episode is None or episode.deleted_at is not None:
        raise LookupError("分集不存在")
    storyboards = _reload_episode_storyboards(db, row.episode_id)
    report = json.loads(row.report_json)
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if not issues:
        raise StoryboardReviewError("当前报告没有需要整改的问题")

    script = _text(episode.script_content, 24000)
    context, scenes = _context(db, episode, storyboards)

    # 1) 自动拆镜：解决「只能压时长却丢台词」的死结
    split_ids = collect_auto_split_ids(storyboards)
    # 报告里明确点名「拆镜」的镜头也纳入（即使当前刚好卡在边界）
    by_number = {int(sb.storyboard_number): sb for sb in storyboards if sb.storyboard_number is not None}
    for item in issues:
        if not isinstance(item, dict):
            continue
        suggestion = str(item.get("suggestion") or "") + str(item.get("problem") or "")
        category = str(item.get("category") or "")
        if "拆" not in suggestion and "拆" not in category and "时长" not in category:
            continue
        numbers = item.get("storyboard_numbers") if isinstance(item.get("storyboard_numbers"), list) else []
        for n in numbers:
            try:
                num = int(n)
            except (TypeError, ValueError):
                continue
            sb = by_number.get(num)
            if sb is not None and sb.id is not None and needs_auto_split(sb):
                if int(sb.id) not in split_ids:
                    split_ids.append(int(sb.id))
    # 仍从大镜号拆起
    if split_ids:
        id_to_no = {int(sb.id): int(sb.storyboard_number or 0) for sb in storyboards if sb.id}
        split_ids = sorted(set(split_ids), key=lambda i: id_to_no.get(i, 0), reverse=True)

    split_changes = apply_auto_splits(db, split_ids, use_llm=False) if split_ids else []
    if split_changes:
        storyboards = _reload_episode_storyboards(db, row.episode_id)
        context, scenes = _context(db, episode, storyboards)

    # 2) 确定性字段修复
    det_changes = apply_deterministic_fixes(storyboards, script, scenes)

    # 3) 画风 + 角色关联
    style_changes: list[dict] = []
    try:
        from app.models.domain import Drama
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_AUDIT

        drama = db.get(Drama, episode.drama_id)
        contract = compose(db=db, drama=drama, stage=STAGE_AUDIT)
        style_changes = apply_style_prompt_fixes(
            storyboards,
            must_include=contract.must_include,
            must_exclude=contract.must_exclude,
        )
    except Exception:  # noqa: BLE001
        style_changes = []

    char_changes = apply_character_link_fixes(db, storyboards)

    # 4) LLM 语义整改（台词对齐、动作可拍等；拆镜已由上一步完成）
    base_url, api_key, model = resolve_llm(db)
    llm_changes: list[dict] = []
    llm_summary = ""
    unresolved_llm: list[str] = []
    try:
        prompt = (
            "你是分镜整改 Agent。根据审核问题修改现有镜头字段。\n"
            "硬约束：\n"
            "1) 系统已自动拆过超长镜；你只改字段，不要输出拆镜指令。\n"
            "2) 不改变剧本事实；台词尽量使用剧本原文（可保留「角色名：」前缀）。\n"
            "3) duration 只能是 1–5 的整数；单镜台词宜 ≤20 字量级。\n"
            "4) location 必须使用项目场景资产列表中的名称（可互为包含的也要改成资产名原文）。\n"
            "5) 动作/结果必须可拍摄，禁止只有抽象形容词。\n"
            "6) image_prompt/video_prompt 用中文视觉描述，禁止字幕/水印/UI 字样。\n"
            "7) 只修改能真正解决问题的字段；不要为了改而改。\n"
            "8) 若某问题仍无法仅靠改字段解决（缺资产/需人工创作决策），写入 unresolved。\n"
            "输出 JSON：{\"updates\":[{\"storyboard_number\":1,\"changes\":{\"action\":\"…\",\"duration\":5}}],"
            "\"summary\":\"本次整改说明\",\"unresolved\":[\"无法自动解决的问题\"]}。\n"
            f"允许修改字段：{', '.join(sorted(EDITABLE_FIELDS))}\n"
            f"用户补充要求：{_text(instruction, 1000) or '无'}\n"
            f"审核问题：{json.dumps(issues, ensure_ascii=False)}\n"
            f"本次已自动拆镜：{json.dumps([c.get('after') for c in split_changes], ensure_ascii=False)}\n"
            f"场景资产名称：{json.dumps([s['location'] for s in context['assets']['scenes']], ensure_ascii=False)}\n"
            f"项目数据：{json.dumps(context, ensure_ascii=False)}"
        )
        raw = chat_text(
            [
                {
                    "role": "system",
                    "content": (
                        "你是严谨的短剧分镜整改 Agent。"
                        "优先修复：台词对齐剧本、场景名对齐资产、动作可拍、时长 1–5 秒、景别节奏、提示词可出片。"
                        "只输出 JSON。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            base_url, api_key, model, temperature=0.15, timeout=180.0, response_format={"type": "json_object"},
        )
        data = _json_object(raw)
        updates = data.get("updates")
        llm_summary = _text(str(data.get("summary") or ""), 800)
        if isinstance(data.get("unresolved"), list):
            unresolved_llm = [_text(str(x), 400) for x in data["unresolved"] if _text(str(x), 400)]
        if isinstance(updates, list):
            by_number = {sb.storyboard_number: sb for sb in storyboards}
            for update in updates:
                if not isinstance(update, dict) or not isinstance(update.get("changes"), dict):
                    continue
                try:
                    number = int(update.get("storyboard_number"))
                except (TypeError, ValueError):
                    continue
                sb = by_number.get(number)
                if sb is None:
                    continue
                before: dict[str, object] = {}
                after: dict[str, object] = {}
                for field, value in update["changes"].items():
                    pair = _apply_field_change(sb, field, value)
                    if pair:
                        before[field], after[field] = pair
                if after:
                    llm_changes.append({
                        "storyboard_id": sb.id,
                        "storyboard_number": number,
                        "source": "llm",
                        "before": before,
                        "after": after,
                    })
        else:
            unresolved_llm.append("整改 Agent 未返回有效的 updates 列表")
    except Exception as exc:  # noqa: BLE001 — 确定性/拆镜修复仍可提交
        unresolved_llm.append(f"LLM 整改跳过：{exc}")

    audit_changes = _merge_audit_changes(
        [split_changes, det_changes, style_changes, char_changes, llm_changes]
    )

    # 5) 改后立刻结构性 + 风格重验（语义需用户点「重新审核」）
    residual = structural_issues(storyboards, script, scenes)
    try:
        from app.models.domain import Drama
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_AUDIT

        drama = db.get(Drama, episode.drama_id)
        contract = compose(db=db, drama=drama, stage=STAGE_AUDIT)
        residual.extend(
            style_prompt_issues(
                storyboards,
                must_include=contract.must_include,
                must_exclude=contract.must_exclude,
            )
        )
    except Exception:  # noqa: BLE001
        pass
    residual = merge_issues(residual)

    if not audit_changes:
        if residual:
            raise StoryboardReviewError(
                "当前问题无法仅靠自动整改解决（可能缺场景资产或需人工创作决策）。"
                "请补齐场景/角色资产，或到分镜台手改后重新审核。"
            )
        raise StoryboardReviewError("整改 Agent 没有生成可应用的修改")

    grade, counts = grade_issues(residual)
    unresolved_notes = list(unresolved_llm)
    for item in residual:
        suggestion = item.get("suggestion") or ""
        category = item.get("category") or ""
        shots = item.get("storyboard_numbers")
        if "拆" in suggestion and not any(
            c.get("source") == "auto_split" or (c.get("after") or {}).get("split_parts")
            for c in split_changes
        ):
            unresolved_notes.append(f"镜头 {shots}: {category}（仍需拆镜）")
        elif category in ("场景资产关联",) and "资产" in suggestion:
            unresolved_notes.append(f"镜头 {shots}: {category}（请先在资产库补场景）")

    seen_u: set[str] = set()
    unresolved_clean: list[str] = []
    for note in unresolved_notes:
        if note and note not in seen_u:
            seen_u.add(note)
            unresolved_clean.append(note)

    split_ok = sum(1 for c in split_changes if (c.get("after") or {}).get("split_parts"))
    fixed_count = len(audit_changes)
    residual_count = len(residual)
    auto_summary = (
        f"已自动处理 {fixed_count} 处（其中拆镜 {split_ok} 镜）；硬规则剩余 {residual_count} 条。"
        + (
            " 仍有需补资产或人工决策的问题。"
            if residual_count
            else " 硬规则已清空，建议点「重新审核」做语义复查。"
        )
    )
    summary = llm_summary or auto_summary

    report["original_issues"] = issues
    report["issues"] = residual
    report["decisions"] = report.get("decisions") if isinstance(report.get("decisions"), list) else []
    report["remediation"] = {
        "applied_at": datetime.now(UTC).isoformat(),
        "model": model,
        "instruction": _text(instruction, 1000) or None,
        "summary": summary,
        "auto_summary": auto_summary,
        "changes": audit_changes,
        "deterministic_count": len(det_changes) + len(style_changes) + len(char_changes),
        "split_count": split_ok,
        "llm_count": len(llm_changes),
        "unresolved": unresolved_clean,
        "needs_semantic_rereview": True,
        "needs_reimage": split_ok > 0 or any(
            "image_prompt" in (c.get("after") or {}) for c in audit_changes
        ),
        "note": (
            "问题清单已替换为「整改后硬规则重验」结果；语义问题请点重新审核。"
            + (" 拆镜后请批量出图并重配音。" if split_ok else "")
        ),
    }
    row.grade = grade
    row.summary = _text(f"[已整改] {summary}", 800)
    row.severe_count = counts["severe"]
    row.medium_count = counts["medium"]
    row.minor_count = counts["minor"]
    row.report_json = json.dumps(report, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return {
        "review": review_view(row),
        "changed_count": fixed_count,
        "changes": audit_changes,
        "residual_count": residual_count,
        "unresolved": unresolved_clean,
        "split_count": split_ok,
    }
