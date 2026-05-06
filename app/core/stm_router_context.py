"""STM for intent router: keep recent 3 dialogues as a tail + budgeted summary of older turns."""
from __future__ import annotations

import copy
from typing import Any

from langchain_openai import ChatOpenAI

from app.agent import prompts as agent_prompts
from app.config.settings import Settings
from app.core.resilience import PipelineMetrics, RetryPolicy, call_with_retry
from app.integrations.lc_run import run_llm_text

# 策略 A：完整保留最近 3 条消息（通常为 上一轮 user → assistant → 当前 user）
VERBATIM_TAIL_SIZE = 3

_MAX_PREPARE_ITERATIONS = 32


def count_text_tokens(text: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def max_router_input_tokens(settings: Settings) -> int:
    return max(
        1024,
        int(settings.stm_router_context_limit)
        - int(settings.stm_router_reserved_output_tokens)
        - int(settings.stm_router_buffer_tokens),
    )


def estimate_router_prompt_tokens(user_text: str, session_context: dict[str, Any]) -> int:
    sys_t = agent_prompts.intent_router_system()
    usr_t = agent_prompts.intent_router_user(user_text, session_context)
    return count_text_tokens(sys_t) + count_text_tokens(usr_t)


def split_verbatim_tail_strategy_a(
    turns: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    norm: list[dict[str, str]] = [
        {"role": str(t.get("role", "")), "content": str(t.get("content", ""))} for t in turns
    ]
    if len(norm) <= VERBATIM_TAIL_SIZE:
        return [], norm
    return norm[:-VERBATIM_TAIL_SIZE], norm[-VERBATIM_TAIL_SIZE:]


def format_turns_transcript(turns: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for t in turns:
        r = (t.get("role") or "").strip()
        c = (t.get("content") or "").strip()
        if c:
            lines.append(f"{r}: {c}")
    return "\n".join(lines)


# 如果最近三次对话依旧超过最大token，则强行截断
def _truncate_verbatim_tail_for_budget(
    tail: list[dict[str, str]],
    user_text: str,
    base_snapshot: dict[str, Any],
    summary: str,
    max_prompt_tokens: int,
) -> list[dict[str, str]]:
    out = copy.deepcopy(tail)
    for _ in range(64):
        ctx: dict[str, Any] = {**base_snapshot, "dialogue_summary": summary, "recent_dialogue": out}
        if estimate_router_prompt_tokens(user_text, ctx) <= max_prompt_tokens:
            return out
        best_i, best_len = -1, -1
        for i, t in enumerate(out):
            L = len(t.get("content") or "")
            if L > best_len:
                best_len = L
                best_i = i
        if best_i < 0 or best_len < 64:
            return out
        c = out[best_i]["content"]
        cut = max(48, (best_len * 2) // 3)
        out[best_i]["content"] = (c[:cut].rstrip() + "\n[truncated]").strip()
    return out


def prepare_stm_router_context(
    *,
    llm: ChatOpenAI,
    user_text: str,
    base_snapshot: dict[str, Any],
    chat_turns: list[dict[str, Any]],
    dialogue_summary: str,
    settings: Settings,
    retry_policy: RetryPolicy,
    metrics: PipelineMetrics | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Build session_context for the intent router under token budget.
    Mutates chat_turns in place: after each compress, drops messages older than the
    last VERBATIM_TAIL_SIZE turns (they are merged into dialogue_summary).
    """
    max_in = max_router_input_tokens(settings)
    summary = (dialogue_summary or "").strip()
    base = dict(base_snapshot)

    for _ in range(_MAX_PREPARE_ITERATIONS):
        head, tail = split_verbatim_tail_strategy_a(chat_turns)
        ctx: dict[str, Any] = {**base, "dialogue_summary": summary, "recent_dialogue": tail}
        if estimate_router_prompt_tokens(user_text, ctx) <= max_in:
            return ctx, summary

        if head:
            head_txt = format_turns_transcript(head)

            def _compress() -> str:
                return run_llm_text(
                    llm,
                    agent_prompts.stm_dialogue_compress_system(),
                    agent_prompts.stm_dialogue_compress_user(summary, head_txt),
                )

            outcome = call_with_retry("stm_compress", _compress, retry_policy)
            if metrics:
                metrics.add("stm_compress", outcome)
            summary = outcome.value.strip()
            if len(chat_turns) > VERBATIM_TAIL_SIZE:
                del chat_turns[:-VERBATIM_TAIL_SIZE]
            continue

        ctx = {**base, "dialogue_summary": summary, "recent_dialogue": tail}
        if estimate_router_prompt_tokens(user_text, ctx) <= max_in:
            return ctx, summary

        if summary and count_text_tokens(summary) > max(256, max_in // 6):
            target_chars = max(400, max_in * 2)

            def _shorten() -> str:
                return run_llm_text(
                    llm,
                    agent_prompts.stm_dialogue_shorten_system(),
                    agent_prompts.stm_dialogue_shorten_user(summary, target_chars),
                )

            outcome = call_with_retry("stm_shorten_summary", _shorten, retry_policy)
            if metrics:
                metrics.add("stm_shorten_summary", outcome)
            summary = outcome.value.strip()
            continue

        tail2 = _truncate_verbatim_tail_for_budget(tail, user_text, base, summary, max_in)
        return {**base, "dialogue_summary": summary, "recent_dialogue": tail2}, summary

    _, tail_fallback = split_verbatim_tail_strategy_a(chat_turns)
    tail2 = _truncate_verbatim_tail_for_budget(tail_fallback, user_text, base, summary, max_in)
    return {**base, "dialogue_summary": summary, "recent_dialogue": tail2}, summary