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


class QuestionGeneratorAgent(LlmRoleAgent):
    """角色 1：把 topic + domain 变成若干研究问题。"""

    def __init__(self) -> None:
        super().__init__("QuestionGenerator")

    def run(self, llm: ChatOpenAI, topic: str, domain: str) -> str:
        system = agent_prompts.question_generator_system()
        user = agent_prompts.question_generator_user(topic, domain)
        print("This is QuestionGeneratorAgent")
        return self._invoke_text(llm, system, user)


# 在文件顶部增加类型与函数导入
from langchain_core.tools import BaseTool

from app.integrations.research_agent_run import run_research_with_tools


class ResearchRoleAgent(LlmRoleAgent):
    def __init__(self) -> None:
        super().__init__("Researcher")

    def run(
        self,
        llm: ChatOpenAI,
        topic: str,
        domain: str,
        question: str,
        tools: list[BaseTool] | None = None,
    ) -> str:
        system = agent_prompts.research_agent_system(topic, domain, question)
        user = agent_prompts.research_agent_user(topic, domain, question)

        if tools:
            return run_research_with_tools(llm, tools, system, user)

        return self._invoke_text(llm, system, user)
        
        


class ReportRoleAgent(LlmRoleAgent):
    """角色 3：把多组 Q&A 合成最终报告。"""

    def __init__(self) -> None:
        super().__init__("ReportWriter")

    def run(
        self,
        llm: ChatOpenAI,
        topic: str,
        domain: str,
        question_answers: list[dict],
    ) -> str:
        qa_sections = "\n".join(
            f"<h2>{idx + 1}. {qa['question']}</h2>\n<p>{qa['answer']}</p>"
            for idx, qa in enumerate(question_answers)
        )
        system = agent_prompts.report_agent_system(topic, domain, qa_sections)
        user = agent_prompts.report_agent_user(topic, domain)
        print("This is ReportRoleAgent")
        return self._invoke_text(llm, system, user)


def default_agents() -> tuple[QuestionGeneratorAgent, ResearchRoleAgent, ReportRoleAgent]:
    """流水线里用的默认三个协作角色；以后要换实现可以只改这个工厂。"""
    return (
        QuestionGeneratorAgent(),
        ResearchRoleAgent(),
        ReportRoleAgent(),
    )