"""小说事件图谱：分章 → 抽事件 → 按事件改编剧本。"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Drama, Episode, NovelChapter, NovelEvent
from app.services.compliance import check, enforce
from app.services.llm.client import LLMNotConfigured, chat_text, resolve_llm
from app.services.project_memory import build_project_context_block


def _split_chapters(text: str) -> list[tuple[str, str]]:
    """按「第x章」或空行大段切分；失败则整篇一章。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?=^第[零一二三四五六七八九十百千0-9]+[章节回]\s*)", text, flags=re.M)
    chapters: list[tuple[str, str]] = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        first = part.split("\n", 1)[0][:40]
        title = first if re.match(r"^第", first) else f"第{i + 1}段"
        chapters.append((title, part))
    if not chapters:
        chapters = [("全文", text)]
    return chapters


def _heuristic_events(chapter_text: str, max_events: int = 12) -> list[dict[str, Any]]:
    """无 LLM 时：按段落启发式切事件。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", chapter_text) if p.strip() and len(p.strip()) > 20]
    if not paras:
        paras = [chapter_text[:800]] if chapter_text else []
    events = []
    step = max(1, len(paras) // max_events) if len(paras) > max_events else 1
    chunks: list[str] = []
    buf: list[str] = []
    for i, p in enumerate(paras):
        buf.append(p)
        if len(buf) >= step or i == len(paras) - 1:
            chunks.append("\n".join(buf))
            buf = []
    for i, chunk in enumerate(chunks[:max_events]):
        title = chunk[:24].replace("\n", " ") + ("…" if len(chunk) > 24 else "")
        events.append(
            {
                "title": f"事件{i + 1}：{title}",
                "summary": chunk[:200],
                "characters": "",
                "location": "",
                "conflict": "",
                "emotion": "",
                "key_dialogue": "",
                "raw_excerpt": chunk[:1200],
            }
        )
    return events


async def extract_events_from_text(
    db: Session,
    *,
    drama_id: int,
    text: str,
    chapter_title: str | None = None,
    chapter_number: int = 1,
) -> dict:
    drama = db.get(Drama, drama_id)
    if drama is None or drama.deleted_at is not None:
        raise LookupError("项目不存在")

    compliance = check(text[:4000] if text else "")
    if compliance.blocked:
        enforce.record_violation(db, None, compliance, "novel_import")
        raise ValueError("导入文本触发合规红线")

    chapters_src = [(chapter_title or "第1章", text)] if chapter_title else _split_chapters(text)
    created_events: list[dict] = []
    created_chapters: list[dict] = []

    for idx, (ctitle, ctext) in enumerate(chapters_src):
        ch = NovelChapter(
            drama_id=drama_id,
            chapter_number=chapter_number + idx,
            title=ctitle,
            content=ctext,
            status="extracted",
        )
        db.add(ch)
        db.flush()
        created_chapters.append({"id": ch.id, "title": ch.title, "chapter_number": ch.chapter_number})

        events_data = await _llm_extract_events(db, drama, ctext)
        if not events_data:
            events_data = _heuristic_events(ctext)

        base_n = db.scalars(
            select(NovelEvent)
            .where(NovelEvent.drama_id == drama_id, NovelEvent.deleted_at.is_(None))
            .order_by(NovelEvent.event_number.desc())
        ).first()
        next_num = (base_n.event_number + 1) if base_n else 1

        for j, ev in enumerate(events_data):
            row = NovelEvent(
                drama_id=drama_id,
                chapter_id=ch.id,
                event_number=next_num + j,
                title=str(ev.get("title") or f"事件{next_num + j}")[:120],
                summary=str(ev.get("summary") or "")[:2000],
                characters=_as_str(ev.get("characters")),
                location=str(ev.get("location") or "")[:200] or None,
                conflict=str(ev.get("conflict") or "")[:500] or None,
                emotion=str(ev.get("emotion") or "")[:100] or None,
                key_dialogue=str(ev.get("key_dialogue") or "")[:500] or None,
                raw_excerpt=str(ev.get("raw_excerpt") or "")[:2000] or None,
                sort_order=next_num + j,
                status="pending",
            )
            db.add(row)
            db.flush()
            created_events.append(event_view(row))

    db.commit()
    return {
        "drama_id": drama_id,
        "chapter_count": len(created_chapters),
        "event_count": len(created_events),
        "chapters": created_chapters,
        "events": created_events,
    }


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return "、".join(str(x) for x in value)
    return str(value)


async def _llm_extract_events(db: Session, drama: Drama, chapter_text: str) -> list[dict]:
    ctx = build_project_context_block(db, drama.id)
    excerpt = chapter_text[:6000]
    prompt = (
        f"{ctx}\n"
        "请从以下小说章节中提取 3–12 个叙事事件，严格输出 JSON 数组，每项字段："
        "title, summary, characters(数组或字符串), location, conflict, emotion, key_dialogue, raw_excerpt。\n"
        "只输出 JSON，不要 Markdown。\n\n章节：\n"
        f"{excerpt}"
    )
    try:
        base_url, api_key, model = resolve_llm(db)
        raw = chat_text(
            [
                {"role": "system", "content": "你是短剧改编策划，擅长把小说拆成可拍摄事件。"},
                {"role": "user", "content": prompt},
            ],
            base_url,
            api_key,
            model,
            temperature=0.3,
        )
    except LLMNotConfigured:
        return []
    except Exception:  # noqa: BLE001
        return []
    return _parse_json_list(raw)


def _parse_json_list(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return [x for x in data["events"] if isinstance(x, dict)]
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
            except json.JSONDecodeError:
                return []
    return []


async def adapt_events_to_episode(
    db: Session,
    *,
    drama_id: int,
    event_ids: list[int],
    episode_id: int | None = None,
    episode_title: str | None = None,
) -> dict:
    drama = db.get(Drama, drama_id)
    if drama is None or drama.deleted_at is not None:
        raise LookupError("项目不存在")
    if not event_ids:
        raise ValueError("请选择至少一个事件")

    events = list(
        db.scalars(
            select(NovelEvent).where(
                NovelEvent.drama_id == drama_id,
                NovelEvent.id.in_(event_ids),
                NovelEvent.deleted_at.is_(None),
            )
        ).all()
    )
    events.sort(key=lambda e: (e.sort_order or 0, e.event_number or 0))
    if not events:
        raise LookupError("事件不存在")

    ctx = build_project_context_block(db, drama_id)
    event_block = "\n\n".join(
        f"### 事件{e.event_number} {e.title}\n"
        f"摘要：{e.summary or ''}\n"
        f"人物：{e.characters or ''}\n"
        f"地点：{e.location or ''}\n"
        f"冲突：{e.conflict or ''}\n"
        f"情绪：{e.emotion or ''}\n"
        f"对白：{e.key_dialogue or ''}\n"
        f"原文摘录：{(e.raw_excerpt or '')[:600]}"
        for e in events
    )
    system = "你是西瓜短剧编剧。根据事件图谱写可拍摄短剧分场剧本，不要输出事件以外的情节。"
    user = (
        f"{ctx}\n请将下列事件改编为一集短剧剧本（含场次、人物、动作、对白），总时长适合 2–5 分钟短剧。\n\n"
        f"{event_block}"
    )
    try:
        base_url, api_key, model = resolve_llm(db)
        script = chat_text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            base_url,
            api_key,
            model,
            temperature=0.6,
        )
    except LLMNotConfigured:
        # 无 LLM：拼接事件摘要为可编辑草稿
        script = "【草稿·无 LLM】\n\n" + "\n\n".join(
            f"场次{i + 1} {e.title}\n{e.summary or ''}\n对白：{e.key_dialogue or ''}" for i, e in enumerate(events)
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"改编失败: {exc}") from exc

    comp = check(script)
    if comp.blocked:
        enforce.record_violation(db, None, comp, "event_adapt")
        raise ValueError("改编结果触发合规红线")

    if episode_id:
        ep = db.get(Episode, episode_id)
        if ep is None or ep.drama_id != drama_id:
            raise LookupError("分集不存在")
    else:
        last = db.scalars(
            select(Episode)
            .where(Episode.drama_id == drama_id, Episode.deleted_at.is_(None))
            .order_by(Episode.episode_number.desc())
        ).first()
        num = (last.episode_number + 1) if last else 1
        titles = "、".join((e.title or "")[:12] for e in events[:3])
        ep = Episode(
            drama_id=drama_id,
            episode_number=num,
            title=episode_title or f"事件改编 · {titles}",
            script_content=script,
            status="draft",
        )
        db.add(ep)
        db.flush()
    ep.script_content = script
    for e in events:
        e.episode_id = ep.id
        e.status = "adapted"
    db.commit()
    db.refresh(ep)
    return {
        "episode_id": ep.id,
        "episode_number": ep.episode_number,
        "title": ep.title,
        "script_preview": (script or "")[:500],
        "event_ids": [e.id for e in events],
    }


def list_events(db: Session, drama_id: int) -> list[dict]:
    rows = db.scalars(
        select(NovelEvent)
        .where(NovelEvent.drama_id == drama_id, NovelEvent.deleted_at.is_(None))
        .order_by(NovelEvent.sort_order, NovelEvent.event_number, NovelEvent.id)
    ).all()
    return [event_view(r) for r in rows]


def list_chapters(db: Session, drama_id: int) -> list[dict]:
    rows = db.scalars(
        select(NovelChapter)
        .where(NovelChapter.drama_id == drama_id, NovelChapter.deleted_at.is_(None))
        .order_by(NovelChapter.chapter_number, NovelChapter.id)
    ).all()
    return [
        {
            "id": c.id,
            "drama_id": c.drama_id,
            "chapter_number": c.chapter_number,
            "title": c.title,
            "summary": c.summary,
            "status": c.status,
            "content_preview": (c.content or "")[:200],
        }
        for c in rows
    ]


def event_view(e: NovelEvent) -> dict:
    return {
        "id": e.id,
        "drama_id": e.drama_id,
        "chapter_id": e.chapter_id,
        "event_number": e.event_number,
        "title": e.title,
        "summary": e.summary,
        "characters": e.characters,
        "location": e.location,
        "conflict": e.conflict,
        "emotion": e.emotion,
        "key_dialogue": e.key_dialogue,
        "raw_excerpt": e.raw_excerpt,
        "episode_id": e.episode_id,
        "status": e.status,
        "sort_order": e.sort_order,
    }
