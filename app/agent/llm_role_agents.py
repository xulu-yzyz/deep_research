# app/agent/llm_role_agents.py
from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.agent import prompts as agent_prompts
from app.integrations.lc_run import run_llm_text


class LlmRoleAgent:
    """所有「轻量 agent」的公共父类：统一走一次纯文本 LLM 调用。"""

    def __init__(self, name: str) -> None:
        # name 用于日志、UI、以后分布式追踪；体现「这是哪一个 agent」
        self.name = name

    def _invoke_text(self, llm: ChatOpenAI, system: str, user: str) -> str:
        """真正调用模型的地方；子类或后续可只改这里接入 tools / ReAct。"""
        return run_llm_text(llm, system, user)



class ReportRoleAgent(LlmRoleAgent):
    def __init__(self) -> None:
        super().__init__("ReportWriter")

    def run(
        self,
        llm: ChatOpenAI,
        topic: str,
        domain: str,
        question_answers: list[dict],
        preferences: dict | None = None,
        memory_context: str | None = None,
    ) -> str:
        qa_sections = "\n".join(
            f"<h2>{idx + 1}. {qa['question']}</h2>\n<p>{qa['answer']}</p>"
            for idx, qa in enumerate(question_answers)
        )
        system = agent_prompts.report_agent_system(
            topic,
            domain,
            qa_sections,
            preferences=preferences,
            memory_context=memory_context,
        )
        user = agent_prompts.report_agent_user(topic, domain)
        return self._invoke_text(llm, system, user)

