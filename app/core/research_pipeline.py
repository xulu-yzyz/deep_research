from __future__ import annotations

from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.cache import research_redis_cache
from app.config.settings import Settings
from app.core.research_service import compile_report, generate_questions, research_one_question
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.db import research_repository


def run_questions_phase(
    db: Session,
    uid: int,
    topic: str,
    domain: str,
    *,
    force_new: bool,
    llm: ChatOpenAI,
    settings: Settings,
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics,
    old_question_ids: list[int],
) -> tuple[list[str], list[int], int] | tuple[None, None, None, str]:
    topic, domain = topic.strip(), domain.strip()
    if not topic or not domain:
        return None, None, None, "主题或领域为空。"

    if force_new:
        research_redis_cache.delete_questions_bundle(uid, topic, domain)
        for qid in old_question_ids:
            research_redis_cache.delete_answer(int(qid))

    rq = None if force_new else research_redis_cache.try_get_questions_bundle(uid, topic, domain)
    if rq is not None:
        questions, q_ids, run_id = rq
        return questions, q_ids, run_id

    cached = None if force_new else research_repository.try_get_cached_questions(db, uid, topic, domain)
    if cached is not None:
        questions, q_ids, run_id = cached
        research_redis_cache.set_questions_bundle(
            uid,
            topic,
            domain,
            run_id,
            questions,
            q_ids,
            settings.redis_ttl_questions_seconds,
        )
        return questions, q_ids, run_id

    try:
        run = research_repository.create_research_run(
            db, uid, topic, domain, settings.deepseek_model_id
        )
        db.commit()
        db.refresh(run)
        questions, _ = generate_questions(llm, topic, domain, retry_policy, metrics)
        q_ids = research_repository.save_questions_for_run(db, int(run.id), questions)
        db.commit()
        research_redis_cache.set_questions_bundle(
            uid,
            topic,
            domain,
            int(run.id),
            questions,
            q_ids,
            settings.redis_ttl_questions_seconds,
        )
        return questions, q_ids, int(run.id)
    except Exception as e:
        return None, None, None, str(e)


def run_research_phase(
    db: Session,
    *,
    llm: ChatOpenAI,
    topic: str,
    domain: str,
    questions: list[str],
    q_ids: list[int],
    run_id: int,
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics,
    tavily_api_key: str | None,
    settings: Settings,
) -> tuple[list[dict], str | None]:
    if len(q_ids) != len(questions):
        return [], "question_id 与问题条数不一致。"

    out: list[dict] = []
    for i, question in enumerate(questions):
        qid = int(q_ids[i])
        answer = research_redis_cache.try_get_answer(qid)
        if answer is None:
            answer = research_repository.try_get_cached_answer(db, qid)
            if answer is not None:
                research_redis_cache.set_answer(
                    qid, answer, settings.redis_ttl_answer_seconds
                )
        if answer is None:
            try:
                answer, _ = research_one_question(
                    llm,
                    topic,
                    domain,
                    question,
                    retry_policy,
                    metrics,
                    tavily_api_key=tavily_api_key,
                )
                research_repository.save_answer(db, int(run_id), qid, answer)
                db.commit()
                research_redis_cache.set_answer(
                    qid, answer, settings.redis_ttl_answer_seconds
                )
            except Exception as e:
                return [], str(e)
        out.append({"question": question, "answer": answer})
    return out, None


def run_report_phase(
    llm: ChatOpenAI,
    topic: str,
    domain: str,
    question_answers: list[dict],
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics,
) -> tuple[str, str | None]:
    try:
        report, _ = compile_report(
            llm, topic, domain, question_answers, retry_policy, metrics
        )
        return report, None
    except Exception as e:
        return "", str(e)