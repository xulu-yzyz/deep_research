# python -m app.a2a.orchestrator_server
from __future__ import annotations

import uvicorn
from starlette.applications import Starlette
from app.core.trace import trace
from app.a2a.json_http_routes import mount_json_invoke_routes
from app.config.settings import get_settings
from app.integrations.llm_client import build_llm
from app.memory import memory_tools
from app.integrations.memory_agent_run import run_memory_commit_with_tools
from app.core.research_state import ResearchState
from app.core.agentic_research_loop import AgenticResearchLoop
from app.core.working_memory import ensure_working_memory
def _extract_preferences_from_memories(recall_items: list[dict]) -> dict:
    prefs: dict = {}
    for m in recall_items:
        name = str(m.get("name", "")).strip().lower()
        content = str(m.get("content", "")).strip().lower()

        text = f"{name}\n{content}"

        if "report_style" in text or "report style" in text:
            if "mckinsey" in text:
                prefs["report_style"] = "mckinsey"
            elif "academic" in text:
                prefs["report_style"] = "academic"
            elif "investor" in text:
                prefs["report_style"] = "investor_memo"
            elif "professional" in text:
                prefs["report_style"] = "professional"

        if "language" in text:
            if "zh" in text or "chinese" in text or "中文" in text:
                prefs["language"] = "zh"
            elif "en" in text or "english" in text or "英文" in text:
                prefs["language"] = "en"

        if "answer_tone" in text or "answer tone" in text or "tone" in text:
            if "conservative" in text or "保守" in text:
                prefs["answer_tone"] = "conservative"
            elif "decisive" in text or "果断" in text:
                prefs["answer_tone"] = "decisive"
            elif "neutral" in text or "中性" in text:
                prefs["answer_tone"] = "neutral"

    return prefs


def _run_pipeline_blocking(payload: dict) -> dict:
    settings = get_settings()
    uid = int(payload["uid"])
    topic = str(payload["topic"]).strip()
    domain = str(payload["domain"]).strip()
    stages = list(payload.get("stages") or [])
    force_new = bool(payload.get("force_new"))
    old_qids = [int(x) for x in (payload.get("old_question_ids") or [])]
    tavily = payload.get("tavily_api_key")
    if tavily is not None:
        tavily = str(tavily).strip() or None
    working_memory = ensure_working_memory(payload.get("working_memory"))
    

    out: dict = {
        "error": None,
        "questions": [],
        "question_ids": [],
        "run_id": None,
        "question_answers": [],
        "report": "",
        "research_complete": False,
        "memory_recall": [],
        "memory_commit": None,
    }

    
    
    recall = memory_tools.search_user_memory(
        user_id=uid,
        query=f"{topic} {domain} user preference report writing language tone style structure",
        mem_type="user",
        top_k=8,
    )
    recall_items = (recall.data or {}).get("items", []) if recall.ok else []
    out["memory_recall"] = recall_items

    memory_context = "\n\n".join(
        [
            f"[{m.get('type','user')}] {m.get('name','')}\n{m.get('content','')}"
            for m in recall_items
            if (m.get("content") or "").strip()
        ]
    ).strip()

    # 不再做 user_preferences 的枚举提取
    user_preferences = {}

    user_preferences = _extract_preferences_from_memories(recall_items)

    for stage in stages:
        trace("orchestrator", "stage开始", stage=stage)
        if stage == "memory_commit":
            # 在 report 成功后再 commit 记忆
            if not out.get("research_complete") or not out.get("report"):
                continue

            # 若服务端没配置 API key，这一步可安全跳过
            if not settings.deepseek_api_key:
                out["memory_commit"] = {
                    "ok": False,
                    "skipped": True,
                    "reason": "missing DEEPSEEK_API_KEY",
                }
                continue

            try:
                llm_for_memory = build_llm(
                    api_key=settings.deepseek_api_key,
                    model_id=settings.deepseek_model_id,
                    base_url=settings.deepseek_base_url,
                )
                planner_input = (
                    f"user_request={payload.get('user_request', '')}\n"
                    f"topic={topic}\n"
                    f"domain={domain}\n"
                    f"report_excerpt={str(out.get('report', ''))[:1500]}\n"
                )
                

                out["memory_commit"] = run_memory_commit_with_tools(
                    llm_for_memory, uid, planner_input
                )
            except Exception as e:
                out["memory_commit"] = {"ok": False, "error": str(e)}
        elif stage == "agentic_research":
            if not settings.deepseek_api_key:
                out["error"] = "missing DEEPSEEK_API_KEY"
                break

            llm = build_llm(
                api_key=settings.deepseek_api_key,
                model_id=settings.deepseek_model_id,
                base_url=settings.deepseek_base_url,
            )

            state = ResearchState(
                uid=uid,
                topic=topic,
                domain=domain,
                user_request=str(payload.get("user_request", "")),
                preferences=user_preferences,
                memory_context=memory_context,
                working_memory=working_memory,
                session_constraints=working_memory.get("session_constraints", {}),
            )

            loop = AgenticResearchLoop()
            state = loop.run(
                llm,
                state,
                tavily_api_key=tavily,
                rag_enabled=bool(payload.get("rag_enabled")),
                rag_persist_directory=str(payload.get("rag_persist_directory") or ".rag/chroma"),
                rag_collection_name=str(payload.get("rag_collection_name") or "research_docs"),
            )

            out["questions"] = [t.question for t in state.tasks]
            out["question_answers"] = [
                {"question": t.question, "answer": t.answer}
                for t in state.tasks
                if t.status == "done"
            ]
            out["report"] = state.report
            out["research_complete"] = state.status == "done"
            out["working_memory"] = state.working_memory
        trace("orchestrator", "stage 结束", stage=stage, error=out.get("error"))
            
    # 兼容：即使 stages 未显式包含 memory_commit，也在 report 成功后尝试一次自动提交
    if out.get("research_complete") and out.get("memory_commit") is None:
        out["memory_commit"] = {"ok": True, "skipped": True, "reason": "no_memory_commit_stage"}



    return out


def build_app() -> Starlette:
    return Starlette(routes=mount_json_invoke_routes(_run_pipeline_blocking))


if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(build_app(), host="127.0.0.1", port=int(s.a2a_orchestrator_port))