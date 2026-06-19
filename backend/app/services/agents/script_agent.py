"""编剧 Agent —— 用 script_rewriter skill 把小说原文改写为格式化剧本。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.services.llm.client import chat, resolve_llm

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def load_skill(name: str) -> str:
    """读取 skills/<name>/SKILL.md，去掉 frontmatter，返回方法论正文。"""
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text


def generate_script(db: Session, novel_content: str, temperature: float = 0.7) -> str:
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("script_rewriter")
    messages = [
        {
            "role": "system",
            "content": skill + "\n\n请将用户给出的小说原文改写为上述格式化剧本，直接输出剧本正文，不要任何解释或前后缀。",
        },
        {"role": "user", "content": novel_content},
    ]
    return chat(messages, base_url, api_key, model, temperature=temperature)
