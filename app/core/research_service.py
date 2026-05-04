from app.agent import prompts as agent_prompts
from app.core.resilience import PipelineMetrics, RetryOutcome, RetryPolicy, call_with_retry
from app.integrations.lc_run import run_llm_text
from app.agent.llm_role_agents import (
    QuestionGeneratorAgent,
    ReportRoleAgent,
    ResearchRoleAgent,
    default_agents,
)
from langchain_core.tools import BaseTool
from app.integrations.tavily_search import build_tavily_search_tool

_question_agent, _research_agent, _report_agent = default_agents()

def extract_questions_after_think(text: str) -> str:
    if "</think>" in text:
        return text.split("</think>", 1)[1].strip()
    return text.strip()


def generate_questions(
    model,
    topic: str,
    domain: str,
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics | None = None,
) -> tuple[list[str], RetryOutcome[str]]:
    def _run() -> str:
        return _question_agent.run(model, topic, domain)

    outcome = call_with_retry("generate_questions", _run, retry_policy)
    if metrics:
        metrics.add("generate_questions", outcome)

    questions_text = extract_questions_after_think(outcome.value)
    questions_list = [q.strip() for q in questions_text.split("\n") if q.strip()]
    return questions_list, outcome


def research_one_question(
    model,
    topic: str,
    domain: str,
    question: str,
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics | None = None,
    tavily_api_key: str | None = None,
) -> tuple[str, RetryOutcome[str]]:
    tools: list[BaseTool] | None = None
    if tavily_api_key and tavily_api_key.strip():
        import os
        os.environ["TAVILY_API_KEY"] = tavily_api_key.strip()
        tools = [build_tavily_search_tool()]
    def _run() -> str:
        return _research_agent.run(model, topic, domain, question, tools=tools)

    outcome = call_with_retry("research_one_question", _run, retry_policy)
    if metrics:
        metrics.add("research_one_question", outcome)

    return outcome.value, outcome


def compile_report(
    model,
    topic: str,
    domain: str,
    question_answers: list[dict],
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics | None = None,
) -> tuple[str, RetryOutcome[str]]:
    qa_sections = "\n".join(
        f"<h2>{idx + 1}. {qa['question']}</h2>\n<p>{qa['answer']}</p>"
        for idx, qa in enumerate(question_answers)
    )

    def _run() -> str:
        return _report_agent.run(model, topic, domain, question_answers)

    outcome = call_with_retry("compile_report", _run, retry_policy)
    if metrics:
        metrics.add("compile_report", outcome)

    return outcome.value, outcome