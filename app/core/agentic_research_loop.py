from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.agent.research_planner_agent import ResearchPlannerAgent
from app.agent.research_critic_agent import ResearchCriticAgent
from app.agent.task_research_agent import TaskResearchAgent
from app.agent.llm_role_agents import ReportRoleAgent
from app.core.research_state import ResearchState
from app.integrations.tavily_search import build_tavily_search_tool
from app.core.working_memory import (
    remember_action,
    remember_open_questions,
    snapshot_research_state,
)
from app.integrations.rag_retriever import build_rag_retrieval_tool
from app.core.trace import trace
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
        rag_enabled: bool = False,
        rag_persist_directory: str = ".rag/chroma",
        rag_collection_name: str = "research_docs",
    ) -> ResearchState:

        trace("agentic_research_loop", "Starting research loop", state=state)
        state = self.planner.run(llm, state)
        trace("planner", "done", plan_count=len(state.plan), task_count=len(state.tasks))

        remember_action(
            state.working_memory,
            "PlannerAgent",
            "created_research_plan",
            "\n".join(state.plan),
        )
        state.working_memory["research_state"] = snapshot_research_state(state)
        tools = []
        if rag_enabled:
            tools.append(
                build_rag_retrieval_tool(
                    persist_directory=rag_persist_directory,
                    collection_name=rag_collection_name,
                    metadata_filter={"domain": state.domain} if state.domain else None,
                )
            )
        if tavily_api_key:
            import os
            os.environ["TAVILY_API_KEY"] = tavily_api_key
            tools.append(build_tavily_search_tool())
        tools = tools or None

        while not state.is_done():
            state.iteration += 1
            trace("research_loop", "iteration+1", iteration=state.iteration)
            
            if state.status == "researching":
                pending = state.pending_tasks()
                if not pending:
                    state.status = "critiquing"
                    continue

                task = pending[0]
                remember_action(
                    state.working_memory,
                    "TaskResearchAgent",
                    "start_task",
                    f"{task.id}: {task.question}",
                )

                trace("researcher", "start_task", task_id=task.id, question=task.question)
                self.researcher.run(llm, state, task, tools=tools)
                trace("researcher", "finish_task", task_id=task.id, status=task.status, confidence=task.confidence)

                remember_action(
                    state.working_memory,
                    "TaskResearchAgent",
                    "finish_task",
                    f"{task.id}: confidence={task.confidence}",
                )
                state.working_memory["research_state"] = snapshot_research_state(state)
                continue

            if state.status == "critiquing":
                trace("critic", "start")
                state = self.critic.run(llm, state)
                trace("critic", "done", gaps=len(state.gaps), next_status=state.status)
                remember_action(
                    state.working_memory,
                    "ResearchCriticAgent",
                    "review_research",
                    "\n".join(state.gaps),
                )
                remember_open_questions(state.working_memory, state.gaps)
                state.working_memory["research_state"] = snapshot_research_state(state)
                continue

            if state.status == "writing":
                qa = [
                    {"question": t.question, "answer": t.answer}
                    for t in state.tasks
                    if t.status == "done"
                ]
                trace("writer", "start")
                state.report = self.writer.run(
                    llm,
                    state.topic,
                    state.domain,
                    qa,
                    preferences=state.preferences,
                    memory_context=state.memory_context,
                )
                remember_action(
                    state.working_memory,
                    "ReportRoleAgent",
                    "write_report",
                    f"report_length={len(state.report)}",
                )
                state.working_memory["research_state"] = snapshot_research_state(state)
                state.status = "done"
                break

        return state