from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.agent.research_planner_agent import ResearchPlannerAgent
from app.agent.research_critic_agent import ResearchCriticAgent
from app.agent.task_research_agent import TaskResearchAgent
from app.agent.llm_role_agents import ReportRoleAgent
from app.core.research_state import ResearchState
from app.integrations.tavily_search import build_tavily_search_tool


class AgenticResearchLoop:
    def __init__(self) -> None:
        self.planner = ResearchPlannerAgent()
        self.researcher = TaskResearchAgent()
        self.critic = ResearchCriticAgent()
        self.writer = ReportRoleAgent()

    def run(
        self,
        llm: ChatOpenAI,
        state: ResearchState,
        *,
        tavily_api_key: str | None = None,
    ) -> ResearchState:
        state = self.planner.run(llm, state)

        tools = None
        if tavily_api_key:
            import os

            os.environ["TAVILY_API_KEY"] = tavily_api_key
            tools = [build_tavily_search_tool()]

        while not state.is_done():
            state.iteration += 1

            if state.status == "researching":
                pending = state.pending_tasks()
                if not pending:
                    state.status = "critiquing"
                    continue

                task = pending[0]
                self.researcher.run(llm, state, task, tools=tools)
                continue

            if state.status == "critiquing":
                state = self.critic.run(llm, state)
                continue

            if state.status == "writing":
                qa = [
                    {"question": t.question, "answer": t.answer}
                    for t in state.tasks
                    if t.status == "done"
                ]
                state.report = self.writer.run(
                    llm,
                    state.topic,
                    state.domain,
                    qa,
                    preferences=state.preferences,
                    memory_context=state.memory_context,
                )
                state.status = "done"
                break

        return state