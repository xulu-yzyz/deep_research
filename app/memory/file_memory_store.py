from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any


class FileMemoryStore:
    MAX_INDEX_LINES = 200
    ALLOWED_TYPES = {"user", "feedback", "project", "reference"}

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(__file__).resolve().parent

    def _user_dir(self, user_id: int) -> Path:
        p = self.base_dir / f"u_{int(user_id)}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _safe_name(self, name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "").strip().lower())
        return safe or "memory"

    def _memory_file(self, user_id: int, mem_type: str, name: str) -> Path:
        return self._user_dir(user_id) / f"{mem_type}_{self._safe_name(name)}.md"

    def _index_file(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "MEMORY.md"

    def _lock_file(self, user_id: int) -> Path:
        return self._user_dir(user_id) / ".lock"

    def _acquire_lock(self, user_id: int, timeout_s: float = 3.0) -> None:
        lock = self._lock_file(user_id)
        start = time.time()
        while lock.exists():
            if time.time() - start > timeout_s:
                raise TimeoutError("memory lock timeout")
            time.sleep(0.05)
        lock.write_text(str(time.time()), encoding="utf-8")

    def _release_lock(self, user_id: int) -> None:
        lf = self._lock_file(user_id)
        if lf.exists():
            lf.unlink(missing_ok=True)

    def _atomic_write(self, path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def _to_text(self, mem: dict[str, Any]) -> str:
        tags = mem.get("tags") or []
        tags_line = ",".join([str(t).strip() for t in tags if str(t).strip()])
        return (
            "---\n"
            f"name: {mem.get('name','')}\n"
            f"description: {mem.get('description','')}\n"
            f"type: {mem.get('type','user')}\n"
            f"confidence: {float(mem.get('confidence',0.8)):.3f}\n"
            f"tags: {tags_line}\n"
            f"updated_at: {mem.get('updated_at','')}\n"
            "---\n"
            f"{str(mem.get('content','')).strip()}\n"
        )

    def _from_text(self, text: str) -> dict[str, Any] | None:
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, flags=re.DOTALL)
        if not m:
            return None
        head, body = m.group(1), m.group(2)
        out: dict[str, Any] = {"content": body.strip()}
        for line in head.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
        out["confidence"] = float(out.get("confidence", 0.8))
        tags_raw = str(out.get("tags", "")).strip()
        out["tags"] = [x.strip() for x in tags_raw.split(",") if x.strip()] if tags_raw else []
        return out

    def save_memory(self, user_id: int, memory: dict[str, Any]) -> dict[str, Any]:
        mem_type = str(memory.get("type", "user")).strip().lower()
        if mem_type not in self.ALLOWED_TYPES:
            mem_type = "user"

        row = {
            "name": str(memory.get("name", "")).strip(),
            "description": str(memory.get("description", "")).strip(),
            "type": mem_type,
            "content": str(memory.get("content", "")).strip(),
            "confidence": float(memory.get("confidence", 0.8)),
            "tags": list(memory.get("tags") or []),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if not row["name"] or not row["content"]:
            raise ValueError("name/content required")

        self._acquire_lock(user_id)
        try:
            fp = self._memory_file(user_id, row["type"], row["name"])
            self._atomic_write(fp, self._to_text(row))
            self.rebuild_index(user_id)
        finally:
            self._release_lock(user_id)

        row["file"] = fp.name
        return row

    def list_memories(self, user_id: int, mem_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        udir = self._user_dir(user_id)
        out: list[dict[str, Any]] = []
        for fp in sorted(udir.glob("*.md")):
            if fp.name == "MEMORY.md":
                continue
            parsed = self._from_text(fp.read_text(encoding="utf-8"))
            if not parsed:
                continue
            parsed["file"] = fp.name
            out.append(parsed)

        if mem_type:
            mt = mem_type.strip().lower()
            out = [x for x in out if str(x.get("type", "")).lower() == mt]

        out.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
        return out[: int(limit)]

    def search(self, user_id: int, query: str, mem_type: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        items = self.list_memories(user_id, mem_type=mem_type, limit=500)
        q = (query or "").strip().lower()
        if not q:
            return items[: int(top_k)]

        def score(m: dict[str, Any]) -> float:
            txt = " ".join(
                [
                    str(m.get("name", "")),
                    str(m.get("description", "")),
                    str(m.get("content", "")),
                    " ".join(m.get("tags") or []),
                ]
            ).lower()
            return txt.count(q) * 10.0 + float(m.get("confidence", 0.0)

            )

        ranked = sorted(items, key=score, reverse=True)
        return ranked[: int(top_k)]

    def rebuild_index(self, user_id: int) -> None:
        rows = self.list_memories(user_id, limit=500)
        lines = ["# Memory Index", ""]
        for m in rows:
            lines.append(
                f"- [{m.get('type','user')}] {m.get('name','')} | {m.get('description','')} | conf={float(m.get('confidence',0.0)):.2f}"
            )
            if len(lines) >= self.MAX_INDEX_LINES:
                lines.append(f"... (truncated at {self.MAX_INDEX_LINES} lines)")
                break
        self._atomic_write(self._index_file(user_id), "\n".join(lines) + "\n")