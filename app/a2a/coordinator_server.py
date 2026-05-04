# app/a2a/coordinator_server.py — 单独进程: python -m app.a2a.coordinator_server
from __future__ import annotations

import asyncio
import json
import uvicorn
from starlette.applications import Starlette

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_artifact,
    new_text_message,
)
from a2a.types.a2a_pb2 import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from app.config.settings import get_settings
from app.core import research_pipeline
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.db.session import SessionLocal
from app.integrations.llm_client import build_llm


def _run_pipeline_blocking(payload: dict) -> dict:
    """在线程中执行；返回可 json.dumps 的 dict。"""
    settings = get_settings()
    uid = int(payload["uid"])
    topic = str(payload["topic"]).strip()
    domain = str(payload["domain"]).strip()
    stages = list(payload["stages"])
    force_new = bool(payload.get("force_new"))
    old_qids = [int(x) for x in payload.get("old_question_ids") or []]
    tavily = payload.get("tavily_api_key")
    if tavily is not None:
        tavily = str(tavily).strip() or None

    retry_policy = RetryPolicy(
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
        jitter_seconds=settings.retry_jitter_seconds,
    )
    metrics = PipelineMetrics()

    llm = build_llm(
        api_key=settings.deepseek_api_key,
        model_id=settings.deepseek_model_id,
        base_url=settings.deepseek_base_url,
    )

    out: dict = {
        "error": None,
        "questions": [],
        "question_ids": [],
        "run_id": None,
        "question_answers": [],
        "report": "",
        "research_complete": False,
    }

    db = SessionLocal()
    try:
        questions: list[str] = []
        q_ids: list[int] = []
        run_id: int = 0
        question_answers: list[dict] = []

        for stage in stages:
            if stage == "questions":
                qres = research_pipeline.run_questions_phase(
                    db,
                    uid,
                    topic,
                    domain,
                    force_new=force_new,
                    llm=llm,
                    settings=settings,
                    retry_policy=retry_policy,
                    metrics=metrics,
                    old_question_ids=old_qids,
                )
                if len(qres) == 4:
                    out["error"] = str(qres[3])
                    break
                questions, q_ids, run_id = qres[0], qres[1], qres[2]
                out["questions"] = questions
                out["question_ids"] = q_ids
                out["run_id"] = run_id
                out["question_answers"] = []
                out["report"] = ""
                out["research_complete"] = False

            elif stage == "research":
                if not questions or not q_ids or not run_id:
                    out["error"] = "缺少问题列表或 run，无法调研。"
                    break
                question_answers, err = research_pipeline.run_research_phase(
                    db,
                    llm=llm,
                    topic=topic,
                    domain=domain,
                    questions=questions,
                    q_ids=q_ids,
                    run_id=run_id,
                    retry_policy=retry_policy,
                    metrics=metrics,
                    tavily_api_key=tavily,
                    settings=settings,
                )
                if err:
                    out["error"] = err
                    break
                out["question_answers"] = question_answers

            elif stage == "report":
                qa = list(out.get("question_answers") or [])
                if not qa:
                    out["error"] = "没有 Q&A，无法生成报告。"
                    break
                report, err = research_pipeline.run_report_phase(
                    llm, topic, domain, qa, retry_policy, metrics
                )
                if err:
                    out["error"] = err
                    break
                out["report"] = report
                out["research_complete"] = True
    finally:
        db.close()

    return out


class ResearchCoordinatorExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_text_message(
                        "Running deep research pipeline…",
                        role=__import__("a2a.types.a2a_pb2", fromlist=["Role"]).Role.ROLE_AGENT,
                        context_id=context.context_id,
                        task_id=context.task_id,
                    ),
                ),
            )
        )

        text = get_message_text(context.message)
        payload = json.loads(text)
        result = await asyncio.to_thread(_run_pipeline_blocking, payload)
        body = json.dumps(result, ensure_ascii=False)

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                artifact=new_text_artifact(name="pipeline_result", text=body),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported")


def build_app() -> Starlette:
    settings = get_settings()
    skill = AgentSkill(
        id="deep_research_pipeline",
        name="Deep research pipeline",
        description="Runs questions / research / report stages for one topic+domain.",
        tags=["research"],
        examples=["{...json payload...}"],
    )
    public_agent_card = AgentCard(
        name="DeepResearch Coordinator",
        description="Wraps existing research_pipeline as an A2A agent.",
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False, extended_agent_card=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=f"http://127.0.0.1:{settings.a2a_coordinator_port}",
            )
        ],
        skills=[skill],
    )

    handler = DefaultRequestHandler(
        agent_executor=ResearchCoordinatorExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=public_agent_card,
        extended_agent_card=None,
    )
    routes = []
    routes.extend(create_agent_card_routes(public_agent_card))
    routes.extend(create_jsonrpc_routes(handler, "/"))
    return Starlette(routes=routes)


if __name__ == "__main__":
    settings = get_settings()
    port = getattr(settings, "a2a_coordinator_port", 9999)
    uvicorn.run(build_app(), host="127.0.0.1", port=int(port))