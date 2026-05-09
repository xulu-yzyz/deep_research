from __future__ import annotations

from typing import Any

from app.core.research_state import ResearchState


def empty_working_memory() -> dict[str, Any]:
    return {
        "research_state": None,
        "user_feedback": [],
        "agent_actions": [],
        "tool_observations": [],
        "open_questions": [],
        "session_constraints": {},
    }


def ensure_working_memory(value: dict | None) -> dict[str, Any]:
    wm = empty_working_memory()
    if isinstance(value, dict):
        for key in wm:
            if key in value:
                wm[key] = value[key]
    return wm


def snapshot_research_state(state: ResearchState) -> dict[str, Any]:
    return {
        "topic": state.topic,
        "domain": state.domain,
        "status": state.status,
        "plan": list(state.plan),
        "gaps": list(state.gaps),
        "report_ready": bool(state.report),
        "iteration": state.iteration,
        "tasks": [
            {
                "id": t.id,
                "question": t.question,
                "reason": t.reason,
                "status": t.status,
                "confidence": t.confidence,
                "answer_preview": t.answer[:500],
                "evidence_count": len(t.evidence),
            }
            for t in state.tasks
        ],
    }


def remember_action(wm: dict[str, Any], actor: str, action: str, detail: str = "") -> None:
    wm.setdefault("agent_actions", []).append(
        {
            "actor": actor,
            "action": action,
            "detail": detail[:1000],
        }
    )


def remember_feedback(wm: dict[str, Any], feedback: str) -> None:
    feedback = feedback.strip()
    if feedback:
        wm.setdefault("user_feedback", []).append(feedback)


def remember_open_questions(wm: dict[str, Any], questions: list[str]) -> None:
    existing = set(wm.setdefault("open_questions", []))
    for q in questions:
        q = str(q).strip()
        if q and q not in existing:
            wm["open_questions"].append(q)
            existing.add(q)


def compact_for_prompt(wm: dict[str, Any]) -> str:
    state = wm.get("research_state") or {}
    feedback = wm.get("user_feedback") or []
    actions = wm.get("agent_actions") or []
    observations = wm.get("tool_observations") or []
    open_questions = wm.get("open_questions") or []
    constraints = wm.get("session_constraints") or {}

    return f"""
Current research state:
{state}

User feedback in this session:
{feedback[-10:]}

Recent agent actions:
{actions[-15:]}

Recent tool observations:
{observations[-10:]}

Open questions / known gaps:
{open_questions[-15:]}

Session constraints:
{constraints}
""".strip()