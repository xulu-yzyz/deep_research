from __future__ import annotations

import json
from langchain_openai import ChatOpenAI

from app.integrations.lc_run import run_llm_text
from app.core.research_state import ResearchState, ResearchTask
from app.core.working_memory import compact_for_prompt

def _system_prompt() -> str:
    return """
You are a senior research planner.

Given a user's research goal, create an adaptive research plan.
Do not produce a final answer.
Return JSON only.

Schema:
{
  "plan": ["step 1", "step 2"],
  "tasks": [
    {
      "id": "t1",
      "question": "specific research question",
      "reason": "why this question matters"
    }
  ]
}

Rules:
- Questions should be open-ended, not only yes/no.
- Cover background, evidence, risks, tradeoffs, and implications when relevant.
- Prefer fewer high-value tasks over many shallow tasks.
""".strip()


def _user_prompt(state: ResearchState) -> str:
    wm_context = compact_for_prompt(state.working_memory)

    return f"""
User request:
{state.user_request}

Topic:
{state.topic}

Domain:
{state.domain}

Persistent user memory/preferences:
{state.memory_context}

Short-term working memory:
{wm_context}

Current session constraints:
{state.session_constraints}
""".strip()

class ResearchPlannerAgent:
    def run(self, llm: ChatOpenAI, state: ResearchState) -> ResearchState:
        raw = run_llm_text(llm, _system_prompt(), _user_prompt(state))
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])

        state.plan = [str(x) for x in data.get("plan", [])]
        state.tasks = [
            ResearchTask(
                id=str(t.get("id") or f"t{i + 1}"),
                question=str(t.get("question", "")).strip(),
                reason=str(t.get("reason", "")).strip(),
            )
            for i, t in enumerate(data.get("tasks", []))
            if str(t.get("question", "")).strip()
        ]
        state.status = "researching"
        return state