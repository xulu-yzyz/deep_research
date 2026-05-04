from __future__ import annotations

import hashlib
import json
from typing import Any

from app.cache.redis_client import get_redis_client


def _hash_scope(user_id: int, topic: str, domain: str) -> str:
    raw = f"{user_id}|{topic.strip()}|{domain.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def questions_key(user_id: int, topic: str, domain: str) -> str:
    return f"research:q:{user_id}:{_hash_scope(user_id, topic, domain)}"


def answer_key(question_id: int) -> str:
    return f"research:a:{question_id}"


def try_get_questions_bundle(
    user_id: int, topic: str, domain: str
) -> tuple[list[str], list[int], int] | None:
    r = get_redis_client()
    if r is None:
        return None
    try:
        raw = r.get(questions_key(user_id, topic, domain))
        if not raw:
            return None
        data: dict[str, Any] = json.loads(raw)
        return (
            list(data["questions"]),
            [int(x) for x in data["question_ids"]],
            int(data["run_id"]),
        )
    except Exception:
        return None


def set_questions_bundle(
    user_id: int,
    topic: str,
    domain: str,
    run_id: int,
    questions: list[str],
    question_ids: list[int],
    ttl_seconds: int,
) -> None:
    r = get_redis_client()
    if r is None:
        return
    try:
        payload = json.dumps(
            {
                "run_id": run_id,
                "questions": questions,
                "question_ids": question_ids,
            },
            ensure_ascii=False,
        )
        r.setex(questions_key(user_id, topic, domain), ttl_seconds, payload)
    except Exception:
        pass


def try_get_answer(question_id: int) -> str | None:
    r = get_redis_client()
    if r is None:
        return None
    try:
        raw = r.get(answer_key(question_id))
        if not raw:
            return None
        # 支持存 JSON 或纯文本
        if raw.startswith("{"):
            data = json.loads(raw)
            return str(data.get("answer_text", ""))
        return raw
    except Exception:
        return None


def set_answer(question_id: int, answer_text: str, ttl_seconds: int) -> None:
    r = get_redis_client()
    if r is None:
        return
    try:
        payload = json.dumps({"answer_text": answer_text}, ensure_ascii=False)
        r.setex(answer_key(question_id), ttl_seconds, payload)
    except Exception:
        pass

def delete_questions_bundle(user_id: int, topic: str, domain: str) -> None:
    r = get_redis_client()
    if r is None:
        return
    try:
        r.delete(questions_key(user_id, topic, domain))
    except Exception:
        pass


def delete_answer(question_id: int) -> None:
    r = get_redis_client()
    if r is None:
        return
    try:
        r.delete(answer_key(question_id))
    except Exception:
        pass