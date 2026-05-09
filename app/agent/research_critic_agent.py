from __future__ import annotations

import json
from langchain_openai import ChatOpenAI

from app.core.research_state import ResearchState, ResearchTask
from app.integrations.lc_run import run_llm_text
from app.core.working_memory import compact_for_prompt

def _system_prompt() -> str:
    return """
You are a research quality critic.
Review the current research state and decide whether more research is needed.
Return JSON only.

Schema:
{
  "sufficient": true,
  "gaps": ["gap 1"],
  "follow_up_tasks": [
    {
      "id": "t_extra_1",
      "question": "follow-up question",
      "reason": "why this is needed"
    }
  ]
}
""".strip()


def _user_prompt(state: ResearchState) -> str:
    completed = "\n\n".join(
        f"Question: {t.question}\n"
        f"Answer: {t.answer}\n"
        f"Confidence: {t.confidence}\n"
        f"Evidence count: {len(t.evidence)}"
        for t in state.tasks
        if t.status == "done"
    )

    return f"""
Topic: {state.topic}
Domain: {state.domain}

Plan:
{state.plan}

Completed research:
{completed}

Short-term working memory:
{compact_for_prompt(state.working_memory)}
""".strip()


class ResearchCriticAgent:
    def run(self, llm: ChatOpenAI, state: ResearchState) -> ResearchState:
        raw = run_llm_text(llm, _system_prompt(), _user_prompt(state))
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])

        state.gaps = [str(x) for x in data.get("gaps", [])]

        if data.get("sufficient") is True:
            state.status = "writing"
            return state

        existing_ids = {t.id for t in state.tasks}
        for i, item in enumerate(data.get("follow_up_tasks", [])):
            task_id = str(item.get("id") or f"follow_up_{state.iteration}_{i}")
            if task_id in existing_ids:
                continue
            question = str(item.get("question", "")).strip()
            if not question:
                continue
            state.tasks.append(
                ResearchTask(
                    id=task_id,
                    question=question,
                    reason=str(item.get("reason", "")).strip(),
                )
            )

        state.status = "researching"
        return state