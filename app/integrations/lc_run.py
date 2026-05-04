from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


def _text_from_ai_message(msg: AIMessage) -> str:
    c = msg.content
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "\n".join(parts).strip()
    return str(c).strip()


def run_llm_text(llm: ChatOpenAI, system: str, user: str) -> str:
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    if isinstance(resp, AIMessage):
        return _text_from_ai_message(resp)
    content = getattr(resp, "content", resp)
    return (content or "").strip() if isinstance(content, str) else str(content).strip()