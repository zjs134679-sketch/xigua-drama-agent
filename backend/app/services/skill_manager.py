"""技能库管理 —— 故事类型技能 + 画风技能（Markdown 文件 + 关键词检索）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "skills"
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


def list_agent_skills() -> list[dict]:
    return [s.to_dict() for s in _scan_dir(AGENT_SKILLS_DIR, "agent")]


def list_all_skills() -> list[dict]:
    return list_story_types() + list_agent_skills()


def get_skill(name: str, category: str = "story_type") -> dict | None:
    if category == "story_type":
        base = DATA_DIR / "story_types"
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
        "content": _content_without_frontmatter(text),
    }


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
