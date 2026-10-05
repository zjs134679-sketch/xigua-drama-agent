"""画风技能包加载 —— 西瓜结构：constraint + prompts + direction。"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.domain import ArtStyle
from app.services.art_style_seed import ART_STYLES_DIR, _parse_frontmatter, load_preset_art_styles

PROMPT_FILES = (
    "prompts/character.md",
    "prompts/character_var.md",
    "prompts/scene.md",
    "prompts/scene_var.md",
    "prompts/prop.md",
    "prompts/prop_var.md",
    "prompts/shot_video.md",
)
DIRECTOR_FILES = (
    "direction/planning.md",
    "direction/storyboard.md",
    "direction/storyboard_table.md",
)

_ASSET_PROMPT_MAP = {
    "character": "prompts/character.md",
    "character_derivative": "prompts/character_var.md",
    "scene": "prompts/scene.md",
    "scene_derivative": "prompts/scene_var.md",
    "prop": "prompts/prop.md",
    "prop_derivative": "prompts/prop_var.md",
    "storyboard_video": "prompts/shot_video.md",
    "video": "prompts/shot_video.md",
}


def skill_key_for_style(style: ArtStyle | None) -> str | None:
    """按中文名或已有预设反查技能目录名。"""
    if style is None:
        return None
    for p in load_preset_art_styles():
        if p["name"] == style.name:
            return p["skill_key"]
    # 兜底：直接扫 frontmatter
    if not ART_STYLES_DIR.is_dir():
        return None
    for folder in ART_STYLES_DIR.iterdir():
        if not folder.is_dir():
            continue
        md = folder / "SKILL.md"
        if not md.exists():
            continue
        meta, _ = _parse_frontmatter(md.read_text(encoding="utf-8"))
        if meta.get("display_name") == style.name or meta.get("name") == style.name:
            return folder.name
    return None


def pack_dir(skill_key: str) -> Path:
    return ART_STYLES_DIR / skill_key


def read_pack_file(skill_key: str, relative: str) -> str | None:
    path = pack_dir(skill_key) / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def load_pack(skill_key: str) -> dict | None:
    base = pack_dir(skill_key)
    if not base.is_dir():
        return None
    skill_md = base / "SKILL.md"
    meta: dict = {}
    skill_body = ""
    if skill_md.exists():
        meta, skill_body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    # 约束手册：优先 constraint.md，兼容旧名 prefix.md
    prefix = (
        read_pack_file(skill_key, "constraint.md")
        or read_pack_file(skill_key, "prefix.md")
        or skill_body
    )
    files: dict[str, str] = {}
    for rel in ("README.md", "constraint.md", "prefix.md", *PROMPT_FILES, *DIRECTOR_FILES):
        text = read_pack_file(skill_key, rel)
        if text is not None:
            files[rel] = text
    return {
        "skill_key": skill_key,
        "display_name": meta.get("display_name") or skill_key,
        "description": meta.get("description") or "",
        "tags": meta.get("tags") or [],
        "prompt_suffix": meta.get("prompt_suffix") or "",
        "sort_order": meta.get("sort_order", 0),
        "prefix": prefix,
        "files": files,
        "file_list": sorted(files.keys()),
    }


def art_prompt_template(skill_key: str, asset_type: str) -> str | None:
    rel = _ASSET_PROMPT_MAP.get(asset_type)
    if not rel:
        return None
    return read_pack_file(skill_key, rel)


def extract_video_style_tags(skill_key: str, *, prefer: str = "zh") -> str | None:
    """从 shot_video.md 表格里抽出风格标签（默认中文）。"""
    text = art_prompt_template(skill_key, "video")
    if not text:
        return None
    # 优先 code 标签；否则取表格第二列长文本
    tags = re.findall(r"`([^`]+)`", text)
    if not tags:
        for line in text.splitlines():
            if "|" not in line or line.strip().startswith("|---") or "风格标签" in line:
                continue
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) >= 2 and len(cols[1]) > 12:
                tags.append(cols[1])
    if not tags:
        return None
    zh = [t.strip() for t in tags if re.search(r"[\u4e00-\u9fff]", t)]
    en = [t.strip() for t in tags if not re.search(r"[\u4e00-\u9fff]", t)]
    if prefer == "zh":
        return (zh or en or tags)[0]
    return (en or zh or tags)[0]


def distill_image_constraint(manual: str | None, *, max_chars: int = 900) -> str | None:
    """
    出图用约束摘要：完整手册过长会挤占 Comfy 正向提示词，
    优先截取「出图锚定词 / 必守 / 严禁 / 全局约束」段落。
    """
    if not manual or not manual.strip():
        return None
    text = manual.strip()
    if len(text) <= max_chars:
        return text

    chunks: list[str] = []
    # 按二级标题切
    parts = re.split(r"(?=^##\s+)", text, flags=re.M)
    priority_keys = ("锚定", "必带", "必守", "禁用", "严禁", "约束", "视觉定位", "出图")
    for part in parts:
        if any(k in part[:40] for k in priority_keys):
            chunks.append(part.strip())
    if not chunks:
        # 取前几段非空行
        lines = [ln for ln in text.splitlines() if ln.strip()]
        brief = "\n".join(lines[:40])
        return brief[:max_chars]

    joined = "\n\n".join(chunks)
    if len(joined) > max_chars:
        return joined[: max_chars - 1].rstrip() + "…"
    return joined


def style_image_constraint(style: ArtStyle | None) -> str | None:
    if style is None:
        return None
    return distill_image_constraint(style.constraint_manual)


def style_video_tags(db: Session | None, style: ArtStyle | None, *, prefer: str = "zh") -> str | None:
    if style is None:
        return None
    key = skill_key_for_style(style)
    if key:
        tags = extract_video_style_tags(key, prefer=prefer)
        if tags:
            return tags
    return style.prompt_suffix or None
