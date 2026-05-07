from __future__ import annotations

from sqlalchemy import select, desc, exists
from sqlalchemy.orm import Session

from app.db.models import (
    ResearchAnswer,
    ResearchQuestion,
    ResearchRun,
    ResearchReport,
    UserPreferenceProfile,
)
from dataclasses import dataclass
from sqlalchemy import select, desc
from sqlalchemy.orm import Session


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

@dataclass
class RunSummary:
    run_id: int
    topic: str
    domain: str
    status: str
    updated_at: object  # datetime
def list_recent_runs(db: Session, user_id: int, limit: int = 20) -> list[RunSummary]:
    rows = db.scalars(
        select(ResearchRun)
        .where(ResearchRun.user_id == int(user_id))
        .order_by(desc(ResearchRun.updated_at), desc(ResearchRun.id))
        .limit(int(limit))
    ).all()
    out: list[RunSummary] = []
    for r in rows:
        out.append(
            RunSummary(
                run_id=int(r.id),
                topic=str(r.topic),
                domain=str(r.domain),
                status=str(r.status),
                updated_at=r.updated_at,
            )
        )
    return out
def load_answers_for_run(db: Session, run_id: int) -> dict[int, str]:
    """返回 {question_id: answer_text}"""
    rows = db.scalars(
        select(ResearchAnswer)
        .where(ResearchAnswer.run_id == int(run_id))
        .order_by(ResearchAnswer.question_id.asc())
    ).all()
    return {int(r.question_id): str(r.answer_text) for r in rows}
    
def load_run_bundle(db: Session, user_id: int, run_id: int) -> dict:
    """恢复整次会话用：topic/domain/questions/qids/qa（report 可选）"""
    run = db.get(ResearchRun, int(run_id))
    if run is None:
        raise ValueError("run_id not found")
    if int(run.user_id) != int(user_id):
        raise ValueError("forbidden: run does not belong to user")
    # questions + qids（你已有 load_questions_from_db 也行；这里直接查模型）
    qrows = db.scalars(
        select(ResearchQuestion)
        .where(ResearchQuestion.run_id == int(run_id))
        .order_by(ResearchQuestion.ordinal.asc())
    ).all()
    questions = [str(q.question_text) for q in qrows]
    qids = [int(q.id) for q in qrows]
    ans_map = load_answers_for_run(db, int(run_id))

    rep = load_report_for_run(db, int(run_id))
    report_body = rep[0] if rep else ""
    report_format = rep[1] if rep else "markdown"

    qa = []
    for q, qid in zip(questions, qids):
        a = ans_map.get(int(qid))
        if a is not None:
            qa.append({"question": q, "answer": a})
    return {
        "run_id": int(run.id),
        "topic": str(run.topic),
        "domain": str(run.domain),
        "status": str(run.status),
        "questions": questions,
        "question_ids": qids,
        "question_answers": qa,
        "report": report_body,
        "report_format": report_format,  # 与 streamlit_app.py 展示结构一致
        # "report": None,  # 若你后续把 report ORM/写入补上，这里再加
    }


def upsert_report(
    db: Session,
    run_id: int,
    body: str,
    *,
    format: str = "html",
) -> None:
    fmt = (format or "html").strip().lower()
    if fmt not in ("html", "markdown"):
        fmt = "html"

    row = db.scalars(
        select(ResearchReport).where(ResearchReport.run_id == int(run_id)).limit(1)
    ).first()

    if row is None:
        row = ResearchReport(run_id=int(run_id), body=body, format=fmt)
        db.add(row)
    else:
        row.body = body
        row.format = fmt


def load_report_for_run(db: Session, run_id: int) -> tuple[str, str] | None:
    row = db.scalars(
        select(ResearchReport).where(ResearchReport.run_id == int(run_id)).limit(1)
    ).first()
    if row is None:
        return None
    return str(row.body), str(row.format)

_ALLOWED_REPORT_STYLE = {"mckinsey", "academic", "investor_memo", "professional"}
_ALLOWED_LANGUAGE = {"zh", "en"}
_ALLOWED_TONE = {"neutral", "conservative", "decisive"}

def _sanitize_preferences(prefs: dict | None) -> dict:
    prefs = prefs or {}
    out: dict[str, str] = {}

    rs = str(prefs.get("report_style", "")).strip().lower()
    lg = str(prefs.get("language", "")).strip().lower()
    tn = str(prefs.get("answer_tone", "")).strip().lower()

    if rs in _ALLOWED_REPORT_STYLE:
        out["report_style"] = rs
    if lg in _ALLOWED_LANGUAGE:
        out["language"] = lg
    if tn in _ALLOWED_TONE:
        out["answer_tone"] = tn

    return out


def load_user_preferences(db: Session, user_id: int) -> dict:
    row = db.get(UserPreferenceProfile, int(user_id))
    if row is None:
        return {}
    return _sanitize_preferences(row.preferences_json)


def upsert_user_preferences(
    db: Session,
    user_id: int,
    prefs: dict,
    *,
    source: str = "manual",
) -> dict:
    clean_new = _sanitize_preferences(prefs)
    row = db.get(UserPreferenceProfile, int(user_id))

    if row is None:
        merged = clean_new
        row = UserPreferenceProfile(
            user_id=int(user_id),
            preferences_json=merged,
            source="manual" if source not in ("manual", "inferred") else source,
        )
        db.add(row)
    else:
        old = _sanitize_preferences(row.preferences_json)
        merged = {**old, **clean_new}
        row.preferences_json = merged
        row.source = "manual" if source not in ("manual", "inferred") else source

    return merged