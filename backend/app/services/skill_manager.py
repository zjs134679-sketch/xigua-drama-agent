"""技能库管理 —— 故事类型技能 + 画风技能（Markdown 文件 + 关键词检索）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.paths import skills_data_dir

DATA_DIR = skills_data_dir()
AGENT_SKILLS_DIR = Path(__file__).resolve().parent / "agents" / "skills"


@dataclass
class SkillMeta:
    name: str
    display_name: str
    description: str
    category: str  # story_type / art_style / agent
    tags: list[str] = field(default_factory=list)
    path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "path": self.path,
        }


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta: dict = {}
    for line in parts[1].strip().split("\n"):
        m = re.match(r"^(\w[\w_-]*):\s*(.*)", line.strip())
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key == "tags":
                meta[key] = [t.strip() for t in value.strip("[]").split(",") if t.strip()]
            else:
                meta[key] = value
    return meta


def _content_without_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) == 3 else text
    return text


def _scan_dir(base: Path, category: str) -> list[SkillMeta]:
    skills: list[SkillMeta] = []
    if not base.exists():
        return skills
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        md = d / "SKILL.md"
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        skills.append(SkillMeta(
            name=d.name,
            display_name=meta.get("display_name", meta.get("name", d.name)),
            description=meta.get("description", ""),
            category=category,
            tags=meta.get("tags", []),
            path=str(d),
        ))
    return skills


def list_story_types() -> list[dict]:
    return [s.to_dict() for s in _scan_dir(DATA_DIR / "story_types", "story_type")]


def list_art_styles() -> list[dict]:
    """画风技能库（data/skills/art_styles 文件驱动）。"""
    return [s.to_dict() for s in _scan_dir(DATA_DIR / "art_styles", "art_style")]


def list_agent_skills() -> list[dict]:
    return [s.to_dict() for s in _scan_dir(AGENT_SKILLS_DIR, "agent")]


def list_all_skills() -> list[dict]:
    return list_story_types() + list_art_styles() + list_agent_skills()


def get_skill(name: str, category: str = "story_type") -> dict | None:
    if category == "story_type":
        base = DATA_DIR / "story_types"
    elif category in ("art_style", "art_styles"):
        base = DATA_DIR / "art_styles"
        category = "art_style"
    elif category == "agent":
        base = AGENT_SKILLS_DIR
    else:
        return None
    md = base / name / "SKILL.md"
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text)
    return {
        "name": name,
        "display_name": meta.get("display_name", meta.get("name", name)),
        "description": meta.get("description", ""),
        "category": category,
        "tags": meta.get("tags", []),
        "prompt_suffix": meta.get("prompt_suffix", ""),
        "sort_order": meta.get("sort_order"),
        "content": _content_without_frontmatter(text),
        "raw": text,
    }


def save_skill(name: str, category: str, content: str) -> dict:
    """在线保存 Skill Markdown（仅允许已有目录，防止任意写路径）。"""
    if category == "story_type":
        base = DATA_DIR / "story_types"
    elif category in ("art_style", "art_styles"):
        base = DATA_DIR / "art_styles"
        category = "art_style"
    elif category == "agent":
        base = AGENT_SKILLS_DIR
    else:
        raise ValueError("不支持的 category")
    # 仅安全名
    if not name or ".." in name or "/" in name or "\\" in name:
        raise ValueError("非法技能名")
    folder = base / name
    if not folder.is_dir():
        raise LookupError(f"技能目录不存在: {category}/{name}")
    md = folder / "SKILL.md"
    text = content if content.endswith("\n") else content + "\n"
    md.write_text(text, encoding="utf-8")
    skill = get_skill(name, category)
    if skill is None:
        raise RuntimeError("保存后读取失败")
    return skill


def search_skills(query: str) -> list[dict]:
    query_lower = query.lower()
    results: list[dict] = []
    for skill in list_all_skills():
        score = 0
        text = f"{skill['name']} {skill['display_name']} {skill['description']} {' '.join(skill.get('tags', []))}"
        if query_lower in text.lower():
            score += 10
        for word in query_lower.split():
            if word in text.lower():
                score += 5
        if score > 0:
            skill["_score"] = score
            results.append(skill)
    results.sort(key=lambda s: -s["_score"])
    return results
