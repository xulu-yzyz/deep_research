
def report_agent_system(
    topic: str,
    domain: str,
    qa_sections: str,
    preferences: dict | None = None,
    memory_context: str | None = None,
) -> str:
    memory_block = ""
    if (memory_context or "").strip():
        memory_block = f"""
Persistent User Memory (cross-session preferences/feedback, free-form):
{memory_context}
"""

    return f"""
You are a sophisticated research assistant.

Priority rules:
1) Explicit instructions in the current request
2) Persistent user memory
3) Your default professional judgement

Use the memory as soft constraints:
- preserve user preferred structure, wording density, evidence style, and language
- if memories conflict, prefer newer/high-confidence memories and current request

{memory_block}

Report structure:
1. Executive Summary/Introduction
2. Research Analysis (narrative, not Q&A format)
3. Conclusion/Implications

Use clear, structured HTML for the report.

Topic: {topic}
Domain: {domain}

Research Questions and Findings:
{qa_sections}
""".strip()

def report_agent_user(topic: str, domain: str) -> str:
    return f"Compile final report for topic={topic}, domain={domain}"


def intent_router_system() -> str:
    return """
You are an intent router for a deep-research web app. Read the user's message, optional dialogue_summary,
optional recent_dialogue (verbatim recent turns), and session snapshot. Classify intent and extract fields.
Respond with ONE JSON object only, no markdown, no extra text.

Allowed intent values:
- full_research: user wants to start structured research (topic + domain).
- off_topic: chit-chat or unrelated to research.
- clarify: not enough to determine topic AND domain for research.

JSON schema (all keys required, use empty string for unused text fields, boolean for need_web_search):
{
  "intent": "<one of the allowed values>",
  "topic": "<short canonical research topic, or empty>",
  "domain": "<field/discipline/industry, or empty>",
  "need_web_search": true,
  "reply_to_user": "<short message to show user, or empty>",
  "clarify_prompt": "<one question to ask user if clarify, or empty>"
}

Rules:
- topic and domain should be concise (a few words to one short phrase), stable for caching.
- dialogue_summary (if non-empty) is a compressed history of older turns. Treat it as background only;
  recent_dialogue is verbatim for the latest stretch and takes precedence over the summary when they conflict.
- Use dialogue_summary + recent_dialogue + snapshot to resolve follow-ups ("change domain to healthcare",
  "主题换成某某"): output the UPDATED merged canonical topic and domain in JSON. If the user only changes
  one slot, fill the other from recent_dialogue or snapshot fields research_topic/research_domain.
- For full_research, whenever the merged topic AND domain are known, set intent=full_research and fill both.
  Use intent=clarify only when BOTH are still unknown.
- For off_topic, set reply_to_user politely; leave topic/domain empty.
""".strip()


def intent_router_user(user_text: str, session_context: dict) -> str:
    import json

    snap = json.dumps(session_context, ensure_ascii=False)
    return f"User message:\n{user_text}\n\nSession snapshot (JSON):\n{snap}"

def stm_dialogue_compress_system() -> str:
    return """
You compress dialogue for an intent-routing model. Output ONE concise Chinese paragraph (or short bullets).
Preserve: research topic/domain changes, user constraints, intent-related cues, and anything needed to
resolve follow-up messages like "change domain to X". Drop small talk. No JSON, no role labels.
""".strip()


def stm_dialogue_compress_user(previous_summary: str, new_turns_transcript: str) -> str:
    ps = (previous_summary or "").strip()
    nt = (new_turns_transcript or "").strip()
    return (
        f"此前摘要（可为空）：\n{ps}\n\n"
        f"需要并入摘要的对话片段（role: 文本）：\n{nt}\n\n"
        "请输出合并后的新摘要。"
    )


def stm_dialogue_shorten_system() -> str:
    return """
You shorten a Chinese dialogue summary. Keep facts needed for routing (topic, domain, user goals, edits).
Output plain text only, no JSON.
""".strip()


def stm_dialogue_shorten_user(summary: str, max_chars_hint: int) -> str:
    s = (summary or "").strip()
    return f"下列摘要过长，请压缩到约 {max_chars_hint} 字符以内，保留路由关键信息：\n\n{s}"