"""Memory commit via LangChain agent: tools only, no extra system prompt."""

from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.integrations.research_agent_run import _log_agent_messages
from app.memory.lc_memory_tools import build_memory_commit_tools


def run_memory_commit_with_tools(llm: ChatOpenAI, user_id: int, user_prompt: str) -> dict:
    """
    Single user message (e.g. planner_input); model may call save/search tools or reply in text.

    Returns dict for orchestrator ``memory_commit`` (saved items parsed from tool results).
    """
    tools = build_memory_commit_tools(user_id)
    agent = create_agent(llm, tools)
    result = agent.invoke({"messages": [("user", user_prompt)]})
    msgs = result.get("messages") or []
    _log_agent_messages(msgs)

    saved: list = []
    errors: list[str] = []
    for m in msgs:
        if not isinstance(m, ToolMessage):
            continue
        if getattr(m, "name", None) != "save_durable_memory":
            continue
        raw = (m.content or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(raw[:500])
            continue
        if data.get("ok"):
            item = data.get("item")
            if item is not None:
                saved.append(item)
        else:
            err = data.get("error")
            if err:
                errors.append(str(err))

    last_text = ""
    for m in reversed(msgs):
        if isinstance(m, AIMessage):
            c = m.content
            if isinstance(c, str) and c.strip():
                last_text = c.strip()
                break
            if isinstance(c, list):
                parts = []
                for block in c:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                if parts:
                    last_text = "\n".join(parts).strip()
                    break

    if errors:
        return {
            "ok": False,
            "skipped": False,
            "data": {"saved": saved, "errors": errors},
            "error": "; ".join(errors),
            "summary": last_text,
        }
    if not saved:
        return {
            "ok": True,
            "skipped": True,
            "reason": "agent:no_save",
            "data": {"saved": [], "errors": []},
            "summary": last_text,
        }
    return {
        "ok": True,
        "skipped": False,
        "data": {"saved": saved, "errors": []},
        "summary": last_text,
    }