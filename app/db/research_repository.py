from __future__ import annotations

from sqlalchemy import select, desc, exists
from sqlalchemy.orm import Session

from app.db.models import ResearchAnswer, ResearchQuestion, ResearchRun


def find_latest_run_with_questions(
    db: Session, user_id: int, topic: str, domain: str
) -> int | None:
    """同一用户 + topic + domain 下，最新一条「已有关联问题」的 run_id。"""
    topic_n, domain_n = topic.strip(), domain.strip()
    subq = (
        select(ResearchRun.id)
        .where(
            ResearchRun.user_id == user_id,
            ResearchRun.topic == topic_n,
            ResearchRun.domain == domain_n,
            exists().where(ResearchQuestion.run_id == ResearchRun.id),
        )
        .order_by(desc(ResearchRun.id))
        .limit(1)
    )
    rid = db.scalars(subq).first()
    return int(rid) if rid is not None else None


def load_questions_from_db(db: Session, run_id: int) -> tuple[list[str], list[int]]:
    rows = db.scalars(
        select(ResearchQuestion)
        .where(ResearchQuestion.run_id == run_id)
        .order_by(ResearchQuestion.ordinal.asc())
    ).all()
    texts = [r.question_text for r in rows]
    ids = [int(r.id) for r in rows]
    return texts, ids


def try_get_cached_questions(
    db: Session, user_id: int, topic: str, domain: str
) -> tuple[list[str], list[int], int] | None:
    run_id = find_latest_run_with_questions(db, user_id, topic, domain)
    if run_id is None:
        return None
    texts, qids = load_questions_from_db(db, run_id)
    if not texts:
        return None
    return texts, qids, run_id


def create_research_run(
    db: Session, user_id: int, topic: str, domain: str, model_id: str | None
) -> ResearchRun:
    run = ResearchRun(
        user_id=user_id,
        topic=topic.strip(),
        domain=domain.strip(),
        status="draft",
        model_id=model_id,
    )
    db.add(run)
    db.flush()
    return run


def save_questions_for_run(db: Session, run_id: int, questions: list[str]) -> list[int]:
    """写入 research_question，返回与 questions 同序的 question id 列表。"""
    ids: list[int] = []
    for i, q in enumerate(questions):
        row = ResearchQuestion(
            run_id=run_id,
            ordinal=i + 1,
            question_text=q.strip(),
        )
        db.add(row)
        db.flush()
        ids.append(int(row.id))
    run = db.get(ResearchRun, run_id)
    if run is not None:
        run.status = "questions_ready"
    return ids


def try_get_cached_answer(db: Session, question_id: int) -> str | None:
    row = db.scalars(
        select(ResearchAnswer).where(ResearchAnswer.question_id == question_id).limit(1)
    ).first()
    if row is None:
        return None
    return row.answer_text


def save_answer(db: Session, run_id: int, question_id: int, answer_text: str) -> None:
    row = ResearchAnswer(
        run_id=run_id,
        question_id=question_id,
        answer_text=answer_text,
    )
    db.add(row)
    run = db.get(ResearchRun, run_id)
    if run is not None and run.status == "questions_ready":
        run.status = "researching"