# # python -m app.a2a.research_agent_server
# from __future__ import annotations

# import asyncio
# import json
# import uvicorn
# from starlette.applications import Starlette
# import os
# os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
# from a2a.server.request_handlers import DefaultRequestHandler
# from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
# from a2a.server.tasks import InMemoryTaskStore
# from a2a.server.agent_execution import AgentExecutor, RequestContext
# from a2a.server.events import EventQueue
# from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
# from a2a.helpers import (
#     get_message_text,
#     new_task_from_user_message,
#     new_text_artifact,
#     new_text_message,
# )
# from a2a.types.a2a_pb2 import (
#     Role,
#     TaskArtifactUpdateEvent,
#     TaskState,
#     TaskStatus,
#     TaskStatusUpdateEvent,
# )

# from app.config.settings import get_settings
# from app.core.resilience import PipelineMetrics, RetryPolicy
# from app.core.research_service import research_one_question
# from app.integrations.llm_client import build_llm


# def _work_single(payload: dict) -> dict:
#     settings = get_settings()
#     topic = str(payload["topic"]).strip()
#     domain = str(payload["domain"]).strip()
#     question = str(payload["question"]).strip()
#     tavily = payload.get("tavily_api_key")
#     if tavily is not None:
#         tavily = str(tavily).strip() or None

#     if not topic or not domain or not question:
#         return {"error": "topic、domain 或 question 为空", "answer": ""}

#     retry_policy = RetryPolicy(
#         timeout_seconds=settings.request_timeout_seconds,
#         max_retries=settings.max_retries,
#         base_delay_seconds=settings.retry_base_delay_seconds,
#         max_delay_seconds=settings.retry_max_delay_seconds,
#         jitter_seconds=settings.retry_jitter_seconds,
#     )
#     metrics = PipelineMetrics()
#     llm = build_llm(
#         api_key=settings.deepseek_api_key,
#         model_id=settings.deepseek_model_id,
#         base_url=settings.deepseek_base_url,
#     )
#     answer, _ = research_one_question(
#         llm, topic, domain, question, retry_policy, metrics, tavily_api_key=tavily
#     )
#     return {"answer": answer}


# class ResearchAgentExecutor(AgentExecutor):
#     async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
#         task = context.current_task or new_task_from_user_message(context.message)
#         await event_queue.enqueue_event(task)

#         await event_queue.enqueue_event(
#             TaskStatusUpdateEvent(
#                 task_id=context.task_id,
#                 context_id=context.context_id,
#                 status=TaskStatus(
#                     state=TaskState.TASK_STATE_WORKING,
#                     message=new_text_message(
#                         "调研单题…",
#                         role=Role.ROLE_AGENT,
#                         context_id=context.context_id,
#                         task_id=context.task_id,
#                     ),
#                 ),
#             )
#         )

#         payload = json.loads(get_message_text(context.message))
#         mode = str(payload.get("mode") or "single").lower()

#         if mode == "batch":
#             # 预留批量：不调用 LLM
#             result = {"error": "batch_mode_not_implemented", "answers": []}
#         else:
#             result = await asyncio.to_thread(_work_single, payload)

#         body = json.dumps(result, ensure_ascii=False)

#         await event_queue.enqueue_event(
#             TaskArtifactUpdateEvent(
#                 task_id=context.task_id,
#                 context_id=context.context_id,
#                 artifact=new_text_artifact(name="research_result", text=body),
#             )
#         )
#         await event_queue.enqueue_event(
#             TaskStatusUpdateEvent(
#                 task_id=context.task_id,
#                 context_id=context.context_id,
#                 status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
#             )
#         )

#     async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
#         raise NotImplementedError("cancel not supported")


# def build_app() -> Starlette:
#     settings = get_settings()
#     port = settings.a2a_research_port
#     skill = AgentSkill(
#         id="research_answer",
#         name="Research one question",
#         description='JSON: {mode:"single", topic, domain, question, tavily_api_key?}. Batch mode returns error only.',
#         tags=["research"],
#         examples=['{"mode":"single","topic":"x","domain":"y","question":"z"}'],
#     )
#     card = AgentCard(
#         name="Research Agent",
#         description="Answers one research question; batch mode stub.",
#         version="0.1.0",
#         default_input_modes=["text/plain"],
#         default_output_modes=["text/plain"],
#         capabilities=AgentCapabilities(streaming=False, extended_agent_card=False),
#         supported_interfaces=[
#             AgentInterface(
#                 protocol_binding="JSONRPC",
#                 url=f"http://127.0.0.1:{port}",
#             )
#         ],
#         skills=[skill],
#     )
#     handler = DefaultRequestHandler(
#         agent_executor=ResearchAgentExecutor(),
#         task_store=InMemoryTaskStore(),
#         agent_card=card,
#         extended_agent_card=None,
#     )
#     routes: list = []
#     routes.extend(create_agent_card_routes(card))
#     routes.extend(create_jsonrpc_routes(handler, "/"))
#     return Starlette(routes=routes)


# if __name__ == "__main__":
#     s = get_settings()
#     uvicorn.run(build_app(), host="127.0.0.1", port=int(s.a2a_research_port))

# python -m app.a2a.research_agent_server
from __future__ import annotations

import uvicorn
from starlette.applications import Starlette

from app.a2a.json_http_routes import mount_json_invoke_routes
from app.config.settings import get_settings
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.core.research_service import research_one_question
from app.integrations.llm_client import build_llm


def _work_single(payload: dict) -> dict:
    settings = get_settings()
    topic = str(payload["topic"]).strip()
    domain = str(payload["domain"]).strip()
    question = str(payload["question"]).strip()
    tavily = payload.get("tavily_api_key")
    if tavily is not None:
        tavily = str(tavily).strip() or None

    if not topic or not domain or not question:
        return {"error": "topic、domain 或 question 为空", "answer": ""}

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
    answer, _ = research_one_question(
        llm, topic, domain, question, retry_policy, metrics, tavily_api_key=tavily
    )
    return {"answer": answer}


def _work(payload: dict) -> dict:
    mode = str(payload.get("mode") or "single").lower()
    if mode == "batch":
        return {"error": "batch_mode_not_implemented", "answers": []}
    return _work_single(payload)


def build_app() -> Starlette:
    return Starlette(routes=mount_json_invoke_routes(_work))


if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(build_app(), host="127.0.0.1", port=int(s.a2a_research_port))