from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI

from app.agent import prompts as agent_prompts
from app.core.resilience import PipelineMetrics, RetryOutcome, RetryPolicy, call_with_retry
from app.integrations.lc_run import run_llm_text

VALID_INTENTS = frozenset(
    {
        "full_research", #用户想开始做深度调研
        "off_topic", #用户想回答一个与主题无关的问题,无需搜索
        "clarify", #信息不够抽 topic+domain
    }
)


@dataclass(frozen=True)
class RoutingDecision:
    intent: str
    topic: str
    domain: str
    need_web_search: bool
    reply_to_user: str
    clarify_prompt: str


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def parse_router_json(raw: str) -> dict[str, Any]:
    s = _strip_json_fence(raw)
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Router response is not a JSON object.")
    return json.loads(s[start : end + 1])


def decision_from_payload(data: dict[str, Any], session_context: dict[str, Any]) -> RoutingDecision:
    intent = str(data.get("intent", "clarify")).strip()
    if intent not in VALID_INTENTS:
        intent = "clarify"

    topic = str(data.get("topic", "")).strip()
    domain = str(data.get("domain", "")).strip()
    need_web = bool(data.get("need_web_search", True))
    reply = str(data.get("reply_to_user", "")).strip()
    clarify = str(data.get("clarify_prompt", "")).strip()

    prev_topic = str(session_context.get("research_topic", "")).strip()
    prev_domain = str(session_context.get("research_domain", "")).strip()


    
    # STM-1: 用户只改「领域」或只改「主题」时，模型可能只填一侧；用会话快照补全另一侧。
    if intent == "full_research":
        if not topic:
            topic = prev_topic
        if not domain:
            domain = prev_domain

    if intent in ("full_research", "regenerate_questions", "quick_answer") and (not topic or not domain):
        if not clarify:
            clarify = "请补充明确的研究主题和所属领域（例如：主题 + 行业/学科）。"
        intent = "clarify"

    return RoutingDecision(
        intent=intent,
        topic=topic,
        domain=domain,
        need_web_search=need_web,
        reply_to_user=reply,
        clarify_prompt=clarify,
    )


def route_user_message(
    model: ChatOpenAI,
    user_text: str,
    session_context: dict[str, Any],
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics | None = None,
) -> tuple[RoutingDecision, RetryOutcome[str]]:
    system = agent_prompts.intent_router_system()
    user_msg = agent_prompts.intent_router_user(user_text.strip(), session_context)

    def _run() -> str:
        return run_llm_text(model, system, user_msg)

    outcome = call_with_retry("intent_router", _run, retry_policy)
    if metrics:
        metrics.add("intent_router", outcome)

    try:
        data = parse_router_json(outcome.value)
        decision = decision_from_payload(data, session_context)
    except (json.JSONDecodeError, ValueError, TypeError):
        decision = RoutingDecision(
            intent="clarify",
            topic="",
            domain="",
            need_web_search=True,
            reply_to_user="无法理解路由结果，请换一种说法或补充主题与领域。",
            clarify_prompt="请用一句话说明想调研的主题以及所属领域。",
        )

    return decision, outcome