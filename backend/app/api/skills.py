"""技能库 API —— 故事类型 / 画风技能 列表+检索。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.skill_manager import (
    get_skill,
    list_all_skills,
    list_agent_skills,
    list_story_types,
    search_skills,
)

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("/story-types")
def api_list_story_types() -> list[dict]:
    return list_story_types()


@router.get("/agents")
def api_list_agent_skills() -> list[dict]:
    return list_agent_skills()


@router.get("")
def api_list_all(category: str | None = None) -> list[dict]:
    if category == "story_type":
        return list_story_types()
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
        from fastapi import HTTPException
        raise HTTPException(404, f"技能不存在: {category}/{name}")
    return skill
