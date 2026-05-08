# python -m app.a2a.orchestrator_server
from __future__ import annotations

import uvicorn
from starlette.applications import Starlette

from app.a2a.a2a_json_client import send_json_message_sync
from app.a2a.json_http_routes import mount_json_invoke_routes
from app.config.settings import get_settings
from app.core import research_pipeline
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.db.session import SessionLocal
from app.integrations.llm_client import build_llm
from app.memory import memory_tools
from app.integrations.memory_agent_run import run_memory_commit_with_tools


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

    retry_policy = RetryPolicy(
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
        jitter_seconds=settings.retry_jitter_seconds,
    )
    metrics = PipelineMetrics()

    q_base = f"http://127.0.0.1:{settings.a2a_question_port}"
    r_base = f"http://127.0.0.1:{settings.a2a_research_port}"
    p_base = f"http://127.0.0.1:{settings.a2a_report_port}"

    def question_list_provider(t: str, d: str) -> list[str]:
        data = send_json_message_sync(q_base, {"topic": t, "domain": d})
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return list(data.get("questions") or [])

    def answer_provider(t: str, d: str, q: str) -> str:
        data = send_json_message_sync(
            r_base,
            {
                "mode": "single",
                "topic": t,
                "domain": d,
                "question": q,
                "tavily_api_key": tavily,
            },
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return str(data.get("answer") or "")

    def report_provider(t: str, d: str, qa: list[dict]) -> str:
        data = send_json_message_sync(
            p_base,
            {"topic": t, "domain": d, "question_answers": qa},
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return str(data.get("report") or "")

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

    db = SessionLocal()
    try:
        questions: list[str] = [str(x).strip() for x in payload.get("questions") or [] if str(x).strip()]
        q_ids: list[int] = [int(x) for x in payload.get("question_ids") or []]
        run_id: int = int(payload.get("run_id") or 0)
        question_answers: list[dict] = list(payload.get("question_answers") or [])

        out["questions"] = questions
        out["question_ids"] = q_ids
        out["run_id"] = run_id or None
        out["question_answers"] = question_answers

        
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
            if stage == "memory_recall":
                # 已在 loop 前执行，这里仅占位兼容
                continue

            if stage == "questions":
                qres = research_pipeline.run_questions_phase(
                    db,
                    uid,
                    topic,
                    domain,
                    force_new=force_new,
                    llm=None,
                    settings=settings,
                    retry_policy=retry_policy,
                    metrics=metrics,
                    old_question_ids=old_qids,
                    question_list_provider=question_list_provider,
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
                    llm=None,
                    topic=topic,
                    domain=domain,
                    questions=questions,
                    q_ids=q_ids,
                    run_id=run_id,
                    retry_policy=retry_policy,
                    metrics=metrics,
                    tavily_api_key=tavily,
                    settings=settings,
                    answer_provider=answer_provider,
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
                if not run_id:
                    out["error"] = "缺少 run_id，无法持久化报告。"
                    break

                report, err = research_pipeline.run_report_phase_and_persist(
                    db,
                    run_id=int(run_id),
                    llm=None,
                    topic=topic,
                    domain=domain,
                    question_answers=qa,
                    retry_policy=retry_policy,
                    metrics=metrics,
                    report_format="markdown",
                    report_provider=report_provider,
                    preferences=user_preferences,
                    memory_context=memory_context,
                )
                if err:
                    out["error"] = err
                    break

                out["report"] = report
                out["research_complete"] = True

            elif stage == "memory_commit":
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

        # 兼容：即使 stages 未显式包含 memory_commit，也在 report 成功后尝试一次自动提交
        if out.get("research_complete") and out.get("memory_commit") is None:
            out["memory_commit"] = {"ok": True, "skipped": True, "reason": "no_memory_commit_stage"}

    finally:
        db.close()

    return out


def build_app() -> Starlette:
    return Starlette(routes=mount_json_invoke_routes(_run_pipeline_blocking))


if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(build_app(), host="127.0.0.1", port=int(s.a2a_orchestrator_port))