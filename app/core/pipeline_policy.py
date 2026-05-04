from __future__ import annotations


def stages_for_intent(intent: str) -> list[str]:
    """
    同一意图对应一键流水线里要跑的阶段（子集 agent）。
    顺序固定：questions -> research -> report。
    """
    if intent == "full_research":
        return ["questions", "research", "report"]
    if intent == "regenerate_questions":
        return ["questions"]
    if intent == "report_only":
        return ["report"]
    if intent == "quick_answer":
        return ["questions", "research"]
    return []