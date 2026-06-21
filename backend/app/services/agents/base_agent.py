"""Agent 编排框架 —— Decision → Execution → Supervision 三层。

提供：
- Tool 定义与注册
- 多轮对话（tool call → result → continue）
- 流式输出（SSE async generator）
- 技能加载（复用 skills/ 目录）
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm.client import LLMNotConfigured, chat, chat_stream, resolve_llm

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# ── Tool ──────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    """Agent 可调用的工具。"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., str]  # 同步函数，接收关键字参数，返回字符串结果

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

# ── Agent ─────────────────────────────────────────────────────────────────

@dataclass
class AgentRunState:
    messages: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    final_content: str = ""
    phase: str = "decide"  # decide → execute → supervise → done

class BaseAgent:
    """可扩展 Agent 基类。

    用法：
        agent = BaseAgent(
            name="剧作家",
            system_prompt="你是短剧编剧...",
            tools=[tool_save_script],
            db=db,
        )
        async for chunk in agent.run_stream("写一个3分钟的都市治愈短剧"):
            # chunk: {"type":"phase","phase":"decide"} | {"type":"text","content":"..."} | ...
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[Tool] | None = None,
        db: Session | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_turns: int = 10,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools: dict[str, Tool] = {t.name: t for t in (tools or [])}
        self.db = db
        self._model = model
        self.temperature = temperature
        self.max_turns = max_turns

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        try:
            _, _, m = resolve_llm(self.db)
            return m
        except LLMNotConfigured:
            return "deepseek-chat"

    @property
    def llm_config(self) -> tuple[str, str, str]:
        return resolve_llm(self.db)

    @classmethod
    def load_skill(cls, name: str) -> str:
        text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                return parts[2].strip()
        return text

    def _build_messages(self, user_input: str) -> list[dict]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]

    def _execute_tool(self, tool_call: dict) -> str:
        name = tool_call.get("function", {}).get("name", "")
        args = tool_call.get("function", {}).get("arguments_parsed", {})
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"未知工具: {name}"})
        try:
            return tool.handler(**args)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    async def run(self, user_input: str, context: list[dict] | None = None) -> dict:
        """非流式多轮运行，返回 {content, messages, tool_calls}。"""
        messages = (context or []) + self._build_messages(user_input)
        state = AgentRunState(messages=messages)

        for _ in range(self.max_turns):
            tools_openai = [t.to_openai() for t in self.tools.values()] if self.tools else None
            base_url, api_key, model = self.llm_config
            result = chat(
                messages=state.messages,
                base_url=base_url,
                api_key=api_key,
                model=model,
                temperature=self.temperature,
                tools=tools_openai,
            )

            state.messages.append({"role": "assistant", "content": result["content"] or ""})

            if result["tool_calls"]:
                state.phase = "execute"
                for tc in result["tool_calls"]:
                    tool_result = self._execute_tool(tc)
                    state.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": tool_result,
                    })
                    state.tool_calls.append(tc)
                continue  # loop back for LLM to process tool results

            state.final_content = result["content"]
            state.phase = "done"
            break

        return {
            "content": state.final_content,
            "messages": state.messages,
            "tool_calls": state.tool_calls,
        }

    async def run_stream(
        self,
        user_input: str,
        context: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """流式多轮运行，逐 chunk yield。

        Chunk 类型：
        - {"type":"phase","phase":"decide"|"execute"|"supervise"}
        - {"type":"text","content":"..."}
        - {"type":"tool_call","function":{"name":"...","arguments":"..."},"id":"..."}
        - {"type":"tool_result","name":"...","result":"..."}
        - {"type":"done","content":"最终文本","messages":[...]}
        - {"type":"error","message":"..."}
        """
        try:
            messages = context.copy() if context else []
            messages += self._build_messages(user_input)

            yield {"type": "phase", "phase": "decide"}

            for turn in range(self.max_turns):
                tools_openai = [t.to_openai() for t in self.tools.values()] if self.tools else None
                base_url, api_key, model = self.llm_config
                content_buffer = ""
                tool_calls_this_turn: list[dict] = []

                async for chunk in chat_stream(
                    messages=messages,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    temperature=self.temperature,
                    tools=tools_openai,
                ):
                    if chunk["type"] == "text":
                        content_buffer += chunk["content"]
                        yield {"type": "text", "content": chunk["content"]}
                    elif chunk["type"] == "tool_call":
                        tool_calls_this_turn.append(chunk)
                        yield chunk
                    elif chunk["type"] == "done":
                        pass  # stream ended

                if tool_calls_this_turn:
                    yield {"type": "phase", "phase": "execute"}
                    messages.append({"role": "assistant", "content": content_buffer or None})
                    for tc in tool_calls_this_turn:
                        tool_result = self._execute_tool(tc)
                        yield {"type": "tool_result", "name": tc["function"]["name"], "result": tool_result}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": tool_result,
                        })
                    continue  # next turn with tool results

                yield {"type": "phase", "phase": "done"}
                yield {"type": "done", "content": content_buffer, "messages": messages}
                return

            yield {"type": "error", "message": f"超出最大轮次 ({self.max_turns})"}

        except Exception as exc:
            yield {"type": "error", "message": str(exc)}

    @staticmethod
    def quick(user_input: str, skill_name: str, db: Session, temperature: float = 0.4, instruction: str = "") -> str:
        """快速单轮调用（无 streaming，无 tool）。兼容旧 Agent 接口。"""
        skill = BaseAgent.load_skill(skill_name)
        system = skill + "\n\n" + instruction if instruction else skill
        base_url, api_key, model = resolve_llm(db)
        result = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_input}],
            base_url, api_key, model, temperature,
        )
        return result["content"]

# ── 组合式 Agent（Decision → Execution → Supervision） ─────────────────

class OrchestratedAgent(BaseAgent):
    """三层 Agent：决策 → 执行 → 监督。

    - decide_agent：分析需求 → 产出执行计划
    - execute_agent：按计划执行 → 产出结果
    - supervise_agent：审核结果 → 通过或修订
    """

    def __init__(
        self,
        name: str,
        decide_prompt: str,
        execute_prompt: str,
        supervise_prompt: str,
        tools: list[Tool] | None = None,
        db: Session | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_turns: int = 10,
    ):
        super().__init__(name, "", tools, db, model, temperature, max_turns)
        self.decide_prompt = decide_prompt
        self.execute_prompt = execute_prompt
        self.supervise_prompt = supervise_prompt

    async def run_stream(
        self, user_input: str, context: list[dict] | None = None
    ) -> AsyncGenerator[dict, None]:
        messages = context.copy() if context else []
        base_url, api_key, model = self.llm_config

        # Phase 1: Decide
        yield {"type": "phase", "phase": "decide"}
        plan_content = ""
        async for chunk in chat_stream(
            messages=[{"role": "system", "content": self.decide_prompt}, {"role": "user", "content": user_input}],
            base_url=base_url, api_key=api_key, model=model, temperature=self.temperature,
        ):
            if chunk["type"] == "text":
                plan_content += chunk["content"]
                yield {"type": "text", "content": chunk["content"]}
            elif chunk["type"] == "done":
                pass

        # Phase 2: Execute
        yield {"type": "phase", "phase": "execute"}
        tools_openai = [t.to_openai() for t in self.tools.values()] if self.tools else None
        exec_msgs = [
            {"role": "system", "content": self.execute_prompt},
            {"role": "user", "content": f"需求：{user_input}\n\n执行计划：{plan_content}"},
        ]
        exec_content = ""
        tool_calls_done: list[dict] = []
        async for chunk in chat_stream(
            messages=exec_msgs, base_url=base_url, api_key=api_key, model=model,
            temperature=self.temperature, tools=tools_openai,
        ):
            if chunk["type"] == "text":
                exec_content += chunk["content"]
                yield {"type": "text", "content": chunk["content"]}
            elif chunk["type"] == "tool_call":
                tool_calls_done.append(chunk)
                yield chunk
            elif chunk["type"] == "done":
                pass

        for tc in tool_calls_done:
            result = self._execute_tool(tc)
            yield {"type": "tool_result", "name": tc["function"]["name"], "result": result}

        # Phase 3: Supervise
        yield {"type": "phase", "phase": "supervise"}
        supervise_content = ""
        async for chunk in chat_stream(
            messages=[
                {"role": "system", "content": self.supervise_prompt},
                {"role": "user", "content": f"执行结果：\n{exec_content}"},
            ],
            base_url=base_url, api_key=api_key, model=model, temperature=0.3,
        ):
            if chunk["type"] == "text":
                supervise_content += chunk["content"]
                yield {"type": "text", "content": chunk["content"]}
            elif chunk["type"] == "done":
                pass

        yield {"type": "done", "content": supervise_content or exec_content, "messages": messages}
