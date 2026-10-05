"""技能库 API —— 故事类型 / Agent 技能 列表+检索+在线编辑。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.skill_manager import (
    get_skill,
    list_all_skills,
    list_agent_skills,
    list_art_styles,
    list_story_types,
    save_skill,
    search_skills,
)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillSaveBody(BaseModel):
    content: str


@router.get("/story-types")
def api_list_story_types() -> list[dict]:
    return list_story_types()


@router.get("/art-styles")
def api_list_art_style_skills() -> list[dict]:
    """文件型画风技能，与 DB `/art-styles` 表互补。"""
    return list_art_styles()


@router.get("/agents")
def api_list_agent_skills() -> list[dict]:
    return list_agent_skills()


@router.get("")
def api_list_all(category: str | None = None) -> list[dict]:
    if category == "story_type":
        return list_story_types()
    if category in ("art_style", "art_styles"):
        return list_art_styles()
    if category == "agent":
        return list_agent_skills()
    return list_all_skills()


@router.get("/search")
def api_search(q: str = Query(..., min_length=1)) -> list[dict]:
    return search_skills(q)


@router.get("/{category}/{name}")
def api_get_skill(category: str, name: str) -> dict:
    skill = get_skill(name, category)
    if skill is None:
        raise HTTPException(404, f"技能不存在: {category}/{name}")
    return skill


@router.put("/{category}/{name}")
def api_save_skill(category: str, name: str, body: SkillSaveBody) -> dict:
    try:
        return save_skill(name, category, body.content)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
