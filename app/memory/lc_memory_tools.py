"""LangChain tools for durable user memory (wraps app.memory.memory_tools)."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from app.memory import memory_tools


def build_memory_commit_tools(user_id: int) -> list:
    """Tools bound to a single user; model may call save and/or search."""

    uid = int(user_id)

    @tool
    def save_durable_memory(
        name: str,
        description: str,
        mem_type: str,
        content: str,
        confidence: float = 0.8,
    ) -> str:
        """Persist a cross-session memory for this user.

        Use for stable preferences, corrections, non-obvious project facts, or
        pointers to external resources. Do not store secrets or ephemeral task state.

        mem_type must be one of: user, feedback, project, reference.
        """
        r = memory_tools.save_user_memory(
            user_id=uid,
            name=name.strip(),
            description=description.strip(),
            mem_type=mem_type.strip().lower(),
            content=content.strip(),
            confidence=float(confidence),
        )
        if r.ok:
            return json.dumps({"ok": True, "item": r.data.get("item")}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": r.error or "unknown"}, ensure_ascii=False)

    @tool
    def search_existing_memories(query: str, mem_type: str | None = None, top_k: int = 5) -> str:
        """Search this user's saved memories to avoid duplicates or refine what to store."""
        r = memory_tools.search_user_memory(
            user_id=uid,
            query=query.strip(),
            mem_type=mem_type.strip().lower() if mem_type else None,
            top_k=int(top_k),
        )
        if r.ok:
            return json.dumps({"ok": True, "items": r.data.get("items", [])}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": r.error or "unknown"}, ensure_ascii=False)

    return [save_durable_memory, search_existing_memories]