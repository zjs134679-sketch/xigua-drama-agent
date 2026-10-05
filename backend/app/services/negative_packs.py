"""负向/保护语统一加载（西瓜自研）。

配置：data/skills/contracts/negative_packs.yaml
出图/出片只读本模块，避免 Skill / 硬编码 / 视频三处漂移。
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.paths import skills_data_dir

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def packs_path() -> Path:
    """优先 JSON（可靠）；兼容同目录 yaml 占位。"""
    root = skills_data_dir() / "contracts"
    for name in ("negative_packs.json", "negative_packs.yaml"):
        p = root / name
        if p.is_file():
            return p
    return root / "negative_packs.json"


def load_packs(*, force: bool = False) -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        path = packs_path()
        if not path.is_file():
            _cache = {"version": 0, "packs": {}, "protection": "", "genre_disable": {}}
            return _cache
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            import json

            data = json.loads(raw)
        else:
            from app.services.style_contract import _simple_yaml_load as _load

            data = _load(raw)
        if not isinstance(data, dict):
            data = {}
        _cache = data
        return _cache


def reload_packs() -> dict[str, Any]:
    return load_packs(force=True)


def _join_pack_items(items: list | str | None) -> str:
    if items is None:
        return ""
    if isinstance(items, str):
        return items.strip().strip("，,")
    parts: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip().strip("，,"))
        elif isinstance(item, list):
            parts.append(_join_pack_items(item))
    return "，".join(p for p in parts if p)


def _genre_blob(genre: str | None, narrative: str | None = None) -> str:
    return f"{genre or ''} {narrative or ''}".lower()


def pack_enabled(pack_name: str, *, genre: str | None = None, narrative: str | None = None) -> bool:
    data = load_packs()
    disable_map = data.get("genre_disable") or {}
    if not isinstance(disable_map, dict):
        return True
    keys = disable_map.get(pack_name) or []
    if not isinstance(keys, list):
        return True
    blob = _genre_blob(genre, narrative)
    if not blob.strip():
        return True
    for key in keys:
        k = str(key).strip().lower()
        if k and k in blob:
            return False
    return True


def compose_negative(
    *pack_names: str,
    genre: str | None = None,
    narrative: str | None = None,
    extra: str | None = None,
) -> str:
    data = load_packs()
    packs = data.get("packs") or {}
    if not isinstance(packs, dict):
        packs = {}
    chunks: list[str] = []
    for name in pack_names:
        if not pack_enabled(name, genre=genre, narrative=narrative):
            continue
        chunk = _join_pack_items(packs.get(name))
        if chunk:
            chunks.append(chunk)
    if extra and extra.strip():
        chunks.append(extra.strip().strip("，,"))
    # 去重片段（按中文逗号切）
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk in chunks:
        for token in chunk.replace(",", "，").split("，"):
            t = token.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            ordered.append(t)
    return "，".join(ordered)


def protection_prompt() -> str:
    data = load_packs()
    return str(data.get("protection") or "").strip()


def empty_plate_prompt() -> str:
    data = load_packs()
    return str(data.get("empty_plate") or "").strip()


def scene_empty_lead() -> str:
    data = load_packs()
    return str(data.get("scene_empty_lead") or "").strip()


def scene_empty_tail() -> str:
    data = load_packs()
    return str(data.get("scene_empty_tail") or "").strip()


def video_cue(name: str) -> str:
    data = load_packs()
    cues = data.get("video_cues") or {}
    if not isinstance(cues, dict):
        return ""
    return str(cues.get(name) or "").strip()


def character_negative(*, genre: str | None = None, narrative: str | None = None) -> str:
    return compose_negative(
        "general",
        "character",
        "character_no_modern_military",
        genre=genre,
        narrative=narrative,
    )


def scene_negative(*, genre: str | None = None, narrative: str | None = None) -> str:
    return compose_negative("general", "scene_people", genre=genre, narrative=narrative)


def storyboard_negative(
    *,
    empty_plate: bool = False,
    genre: str | None = None,
    narrative: str | None = None,
    no_glasses: bool = False,
) -> str:
    names = ["general", "storyboard_people"]
    if empty_plate:
        names.append("scene_people")
    extra = None
    if no_glasses:
        extra = compose_negative("no_glasses", genre=genre, narrative=narrative)
    return compose_negative(*names, genre=genre, narrative=narrative, extra=extra)


def general_negative(*, genre: str | None = None, narrative: str | None = None) -> str:
    return compose_negative("general", genre=genre, narrative=narrative)


def video_negative(
    *,
    empty_plate: bool = False,
    genre: str | None = None,
    narrative: str | None = None,
) -> str:
    names = ["video_base"]
    if empty_plate:
        names.append("video_empty_extra")
    else:
        names.append("video_people_extra")
    return compose_negative(*names, genre=genre, narrative=narrative)


def drama_genre_context(db, drama_id: int | None) -> tuple[str | None, str | None]:
    """从项目读 genre / narrative_tag。"""
    if drama_id is None or db is None:
        return None, None
    try:
        from app.models.domain import Drama
        from app.services.style_composer import drama_bible

        drama = db.get(Drama, drama_id)
        if drama is None:
            return None, None
        genre = drama.genre
        narrative = None
        bible = drama_bible(drama)
        if isinstance(bible, dict):
            narrative = bible.get("narrative_tag") or bible.get("genre")
        return genre, str(narrative) if narrative else None
    except Exception:  # noqa: BLE001
        return None, None


# 兼容旧测试/导入名（惰性求值会在 import 时读表）
@lru_cache(maxsize=1)
def _legacy_snapshot() -> dict[str, str]:
    return {
        "PROTECTION_PROMPT": protection_prompt(),
        "EMPTY_PLATE_PROMPT": empty_plate_prompt(),
        "SCENE_EMPTY_LEAD": scene_empty_lead(),
        "SCENE_EMPTY_TAIL": scene_empty_tail(),
        "GENERAL_NEGATIVE_PROMPT": general_negative(),
        "CHARACTER_NEGATIVE_PROMPT": character_negative(),
        "SCENE_NEGATIVE_PROMPT": scene_negative(),
        "STORYBOARD_NEGATIVE_PROMPT": storyboard_negative(empty_plate=False),
        "STORYBOARD_EMPTY_NEGATIVE": storyboard_negative(empty_plate=True),
    }


def legacy(name: str) -> str:
    return _legacy_snapshot().get(name, "")
