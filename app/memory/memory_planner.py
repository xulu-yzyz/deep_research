from __future__ import annotations

import json
from langchain_openai import ChatOpenAI

from app.integrations.lc_run import run_llm_text


PLANNER_SYSTEM = """
You are a memory planner for a research agent.

Goal:
Extract durable cross-session memories from the latest interaction.
Memories are free-form natural language, not fixed enums.

Save when:
- User expresses stable preferences (format, style, language, structure, tone, evidence needs, etc.)
- User gives repeated corrections or explicit "from now on" instructions

Do NOT save:
- Temporary task state
- Obvious repo code structure
- Secrets/credentials

Return JSON only with this schema:
{
  "should_save": true/false,
  "memories": [
    {
      "name": "short_id_like_report_pref_20260507",
      "description": "one-line summary",
      "type": "user|feedback|project|reference",
      "content": "free-form memory text in natural language",
      "confidence": 0.0
    }
  ]
}
If no memory should be saved, return should_save=false and memories=[].
""".strip()


def _strip_json(raw: str) -> str:
    s = (raw or "").strip()
    if "```" in s:
        s = s.replace("```json", "").replace("```", "").strip()
    if "{" in s and "}" in s:
        s = s[s.find("{") : s.rfind("}") + 1]
    return s


def decide_memory(model: ChatOpenAI, text: str) -> dict:
    raw = run_llm_text(model, PLANNER_SYSTEM, text)
    data = json.loads(_strip_json(raw))

    should_save = bool(data.get("should_save", False))
    memories = data.get("memories") or []

    normalized = []
    for m in memories:
        if not isinstance(m, dict):
            continue
        mm = {
            "name": str(m.get("name", "")).strip(),
            "description": str(m.get("description", "")).strip(),
            "type": str(m.get("type", "user")).strip().lower(),
            "content": str(m.get("content", "")).strip(),
            "confidence": float(m.get("confidence", 0.75)),
        }
        if mm["name"] and mm["content"]:
            normalized.append(mm)

    return {
        "should_save": should_save and len(normalized) > 0,
        "memories": normalized,
    }