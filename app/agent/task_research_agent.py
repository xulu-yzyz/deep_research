from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool

from app.core.research_state import ResearchState, ResearchTask
from app.integrations.research_agent_run import run_research_with_tools
from app.core.trace import trace

def _system_prompt(state: ResearchState, task: ResearchTask) -> str:
    return f"""
You are a deep research agent.

Research topic: {state.topic}
Domain: {state.domain}

Task:
{task.question}

Why this matters:
{task.reason}

Instructions:
- Use private_knowledge_search when private/domain documents may contain relevant evidence.
- Don't Use tavily search tool.
- Prefer current, specific, source-grounded evidence.
- If evidence is weak or conflicting, say so.
- End with:
  Confidence: <0.0-1.0>
  Missing information: <short list or none>
""".strip()


def _user_prompt(task: ResearchTask) -> str:
    return f"Research and answer this task: {task.question}"


class TaskResearchAgent:
    def run(
        self,
        llm: ChatOpenAI,
        state: ResearchState,
        task: ResearchTask,
        tools: list[BaseTool] | None = None,
    ) -> ResearchTask:
        task.status = "running"
        tool_names = [getattr(t, "name", type(t).__name__) for t in tools or []]
        trace("task_research_agent", "运行", task_id=task.id, tool_names=tool_names)
        if tools:
            trace("task_research_agent", "使用工具", tool_names=tool_names)
            answer = run_research_with_tools(
                llm,
                tools,
                _system_prompt(state, task),
                _user_prompt(task),
            )
        else:
            from app.integrations.lc_run import run_llm_text

            answer = run_llm_text(llm, _system_prompt(state, task), _user_prompt(task))

        task.answer = answer
        task.status = "done"
        task.confidence = 0.6
        return task