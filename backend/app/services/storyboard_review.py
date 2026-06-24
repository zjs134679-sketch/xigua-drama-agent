"""分镜监督 Agent：确定性规则 + LLM 语义审核 + 版本化报告。"""
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
            issues.append(_issue("severe", "片段时长", f"镜头 {number} 时长无效。", "设置大于 0 秒的时长。", [number]))
        elif sb.duration > 15:
            issues.append(_issue("severe", "片段时长", f"镜头 {number} 时长 {sb.duration} 秒，超过 15 秒。", "按动作或台词语义拆成连续镜头。", [number]))

        dialogue = _text(sb.dialogue)
        dialogue_chars = len(re.sub(r"[\s，。！？、；：,.!?;:\-—‘’“”\"']", "", dialogue))
        if dialogue_chars:
            minimum = math.ceil(dialogue_chars / 4 + 1)
            if sb.duration < minimum:
                issues.append(_issue("medium", "台词时长", f"镜头 {number} 有约 {dialogue_chars} 字台词，但时长仅 {sb.duration} 秒。", f"将时长提高到至少 {minimum} 秒，或按语义拆镜。", [number]))
            if script and dialogue not in script:
                issues.append(_issue("severe", "台词完整性", f"镜头 {number} 的台词未在剧本中找到逐字对应。", "恢复剧本原文并核对说话人，不要改写或概括。", [number]))
        elif sb.duration > 6:
            issues.append(_issue("medium", "片段时长", f"镜头 {number} 无台词但持续 {sb.duration} 秒。", "压缩到 6 秒以内，或补充持续发生的动作/运镜变化。", [number]))

        if scene_names and _text(sb.location) and sb.location.strip() not in scene_names:
            issues.append(_issue("severe", "场景资产关联", f"镜头 {number} 的场景“{sb.location}”未匹配已有场景资产。", "改用已有场景名称，或先补齐并关联对应场景资产。", [number]))

    for index in range(len(storyboards) - 2):
        group = storyboards[index:index + 3]
        shot_types = [_text(sb.shot_type).lower() for sb in group]
        if shot_types[0] and len(set(shot_types)) == 1:
            numbers = [sb.storyboard_number for sb in group]
            issues.append(_issue("minor", "景别视角错开", f"镜头 {numbers[0]}–{numbers[-1]} 连续使用相同景别“{group[0].shot_type}”。", "至少调整其中一镜的景别或视角，形成视觉节奏。", numbers))
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
        result.append(_issue(severity, _text(str(item.get("category") or "综合审核"), 80), problem, _text(str(item.get("suggestion") or "请按问题描述修订。"), 1200), numbers))
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
    storyboards = db.scalars(select(Storyboard).where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None)).order_by(Storyboard.storyboard_number)).all()
    if not storyboards:
        raise StoryboardReviewError("该分集还没有分镜，请先生成分镜")
    if not _text(episode.script_content):
        raise StoryboardReviewError("该分集还没有剧本，无法核对台词与剧情覆盖")

    context, scenes = _context(db, episode, storyboards)
    structural = structural_issues(storyboards, context["episode"]["script"], scenes)
    base_url, api_key, model = resolve_llm(db)
    skill_path = Path(__file__).resolve().parent / "agents" / "skills" / "storyboard_supervisor" / "SKILL.md"
    prompt = (
        "审核下列整集数据。确定性检查已发现的问题也提供给你，请继续检查语义层面的遗漏，尤其是剧本台词覆盖、"
        "人物不消失、VO 音画同步、资产关联、人物外观混入提示词和镜头连贯性。\n"
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
    row = db.scalars(select(StoryboardReview).where(StoryboardReview.episode_id == episode_id).order_by(StoryboardReview.id.desc())).first()
    return review_view(row) if row else None


def list_storyboard_reviews(db: Session, episode_id: int) -> list[dict]:
    rows = db.scalars(select(StoryboardReview).where(StoryboardReview.episode_id == episode_id).order_by(StoryboardReview.id.desc())).all()
    return [review_view(row) for row in rows]


EDITABLE_FIELDS = {
    "title", "location", "time", "shot_type", "angle", "movement", "action", "result", "atmosphere",
    "image_prompt", "video_prompt", "bgm_prompt", "sound_effect", "dialogue", "description", "duration",
}


def remediate_storyboard_review(
    db: Session,
    review_id: int,
    instruction: str | None = None,
) -> dict:
    """依据指定审核报告修改现有分镜，并把逐字段前后差异写回报告。"""
    row = db.get(StoryboardReview, review_id)
    if row is None:
        raise LookupError("审核报告不存在")
    episode = db.get(Episode, row.episode_id)
    if episode is None or episode.deleted_at is not None:
        raise LookupError("分集不存在")
    storyboards = db.scalars(
        select(Storyboard)
        .where(Storyboard.episode_id == row.episode_id, Storyboard.deleted_at.is_(None))
        .order_by(Storyboard.storyboard_number)
    ).all()
    report = json.loads(row.report_json)
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if not issues:
        raise StoryboardReviewError("当前报告没有需要整改的问题")

    context, _ = _context(db, episode, storyboards)
    base_url, api_key, model = resolve_llm(db)
    prompt = (
        "你是分镜整改 Agent。严格依据审核问题修改现有镜头字段，不新增、删除或重排镜头，不改变剧本事实，"
        "台词只能使用剧本原文。只修改解决问题所必需的字段；用户之后仍会手动编辑。\n"
        "输出 JSON：{\"updates\":[{\"storyboard_number\":1,\"changes\":{\"action\":\"修改后内容\","
        "\"duration\":6}}],\"summary\":\"本次整改说明\"}。\n"
        f"允许修改字段：{', '.join(sorted(EDITABLE_FIELDS))}\n"
        f"用户补充要求：{_text(instruction, 1000) or '无'}\n"
        f"审核问题：{json.dumps(issues, ensure_ascii=False)}\n"
        f"项目数据：{json.dumps(context, ensure_ascii=False)}"
    )
    raw = chat_text(
        [{"role": "system", "content": "你是严谨的短剧分镜整改 Agent，只输出 JSON。"}, {"role": "user", "content": prompt}],
        base_url, api_key, model, temperature=0.2, timeout=180.0, response_format={"type": "json_object"},
    )
    data = _json_object(raw)
    updates = data.get("updates")
    if not isinstance(updates, list):
        raise StoryboardReviewError("整改 Agent 未返回有效的镜头修改")

    by_number = {sb.storyboard_number: sb for sb in storyboards}
    audit_changes: list[dict] = []
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
            if field not in EDITABLE_FIELDS:
                continue
            if field == "duration":
                try:
                    clean_value: object = max(1, min(60, int(value)))
                except (TypeError, ValueError):
                    continue
            elif isinstance(value, str):
                clean_value = value.strip()[:8000]
            else:
                continue
            old_value = getattr(sb, field)
            if old_value == clean_value:
                continue
            before[field] = old_value
            after[field] = clean_value
            setattr(sb, field, clean_value)
        if after:
            audit_changes.append({"storyboard_id": sb.id, "storyboard_number": number, "before": before, "after": after})

    if not audit_changes:
        raise StoryboardReviewError("整改 Agent 没有生成可应用的修改")
    report["remediation"] = {
        "applied_at": datetime.now(UTC).isoformat(),
        "model": model,
        "instruction": _text(instruction, 1000) or None,
        "summary": _text(str(data.get("summary") or "已按审核报告完成一键整改。"), 800),
        "changes": audit_changes,
    }
    row.report_json = json.dumps(report, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return {"review": review_view(row), "changed_count": len(audit_changes), "changes": audit_changes}
