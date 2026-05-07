from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.memory.file_memory_store import FileMemoryStore


store = FileMemoryStore()


@dataclass
class MemoryToolResult:
    ok: bool
    data: dict[str, Any]
    error: str | None = None


def save_user_memory(
    *,
    user_id: int,
    name: str,
    description: str,
    mem_type: str,
    content: str,
    confidence: float = 0.8,
    tags: list[str] | None = None,
) -> MemoryToolResult:
    try:
        row = store.save_memory(
            int(user_id),
            {
                "name": name,
                "description": description,
                "type": mem_type,
                "content": content,
                "confidence": confidence,
                "tags": tags or [],
            },
        )
        return MemoryToolResult(True, {"item": row})
    except Exception as e:
        return MemoryToolResult(False, {}, str(e))


def save_user_memories(
    *,
    user_id: int,
    memories: list[dict],
) -> MemoryToolResult:
    saved: list[dict] = []
    errors: list[str] = []
    for m in memories or []:
        r = save_user_memory(
            user_id=int(user_id),
            name=str(m.get("name", "")),
            description=str(m.get("description", "")),
            mem_type=str(m.get("type", "user")),
            content=str(m.get("content", "")),
            confidence=float(m.get("confidence", 0.75)),
            tags=list(m.get("tags") or []),
        )
        if r.ok:
            saved.append(r.data.get("item", {}))
        else:
            errors.append(r.error or "unknown error")
    return MemoryToolResult(len(errors) == 0, {"saved": saved, "errors": errors}, None if not errors else "; ".join(errors))


def list_user_memories(
    *,
    user_id: int,
    mem_type: str | None = None,
    limit: int = 20,
) -> MemoryToolResult:
    try:
        rows = store.list_memories(int(user_id), mem_type=mem_type, limit=int(limit))
        return MemoryToolResult(True, {"items": rows})
    except Exception as e:
        return MemoryToolResult(False, {}, str(e))


def search_user_memory(
    *,
    user_id: int,
    query: str,
    mem_type: str | None = None,
    top_k: int = 5,
) -> MemoryToolResult:
    try:
        rows = store.search(int(user_id), query=query, mem_type=mem_type, top_k=int(top_k))
        return MemoryToolResult(True, {"items": rows})
    except Exception as e:
        return MemoryToolResult(False, {}, str(e))