"""Agent 对话 API —— SSE 流式 + 普通响应。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.agents.base_agent import BaseAgent, OrchestratedAgent, Tool

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentChatRequest(BaseModel):
    skill_name: str = ""
    system_prompt: str = ""
    message: str
    temperature: float = 0.7
    context: list[dict] | None = None


class AgentQuickRequest(BaseModel):
    skill_name: str
    message: str
    temperature: float = 0.4
    instruction: str = ""


@router.post("/chat")
async def agent_chat(body: AgentChatRequest, db: Session = Depends(get_db)):
    """非流式 Agent 对话。"""
    if body.skill_name:
        system_prompt = BaseAgent.load_skill(body.skill_name)
    elif body.system_prompt:
        system_prompt = body.system_prompt
    else:
        raise HTTPException(400, "需要 skill_name 或 system_prompt")

    agent = BaseAgent(
        name="chat",
        system_prompt=system_prompt,
        db=db,
        temperature=body.temperature,
    )
    result = await agent.run(body.message, context=body.context)
    return {
        "content": result["content"],
        "messages": result["messages"],
        "truncated": result.get("truncated", False),
        "error": result.get("error"),
    }


@router.post("/chat/stream")
async def agent_chat_stream(body: AgentChatRequest, db: Session = Depends(get_db)):
    """SSE 流式 Agent 对话。"""
    if body.skill_name:
        system_prompt = BaseAgent.load_skill(body.skill_name)
    elif body.system_prompt:
        system_prompt = body.system_prompt
    else:
        raise HTTPException(400, "需要 skill_name 或 system_prompt")

    agent = BaseAgent(
        name="chat",
        system_prompt=system_prompt,
        db=db,
        temperature=body.temperature,
    )

    async def generate():
        async for chunk in agent.run_stream(body.message, context=body.context):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/quick")
def agent_quick(body: AgentQuickRequest, db: Session = Depends(get_db)):
    """快速单轮调用（无流式），兼容旧接口。"""
    result = BaseAgent.quick(
        user_input=body.message,
        skill_name=body.skill_name,
        db=db,
        temperature=body.temperature,
        instruction=body.instruction,
    )
    return {"content": result}


@router.get("/skills")
def list_skills():
    """列出可用技能文件。"""
    from pathlib import Path
    skills_dir = Path(__file__).resolve().parent.parent / "services" / "agents" / "skills"
    skills = []
    if skills_dir.exists():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                text = (d / "SKILL.md").read_text(encoding="utf-8")
                first_line = ""
                desc = ""
                for line in text.split("\n"):
                    if line.startswith("# "):
                        first_line = line[2:].strip()
                    elif first_line and line.strip() and not line.startswith("#"):
                        desc = line.strip()
                        break
                skills.append({"name": d.name, "title": first_line or d.name, "description": desc})
    return skills
