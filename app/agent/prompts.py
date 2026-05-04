def question_generator_system() -> str:
    return """
You are an expert at breaking down research topics into specific questions.
Generate exactly 5 specific yes/no research questions about the given topic in the specified domain.
Respond ONLY with the text of the 5 questions formatted as a numbered list, and NOTHING ELSE.
""".strip()


def question_generator_user(topic: str, domain: str) -> str:
    return (
        f"Generate exactly 5 specific yes/no research questions about the topic '{topic}' "
        f"in the domain '{domain}'."
    )


def research_agent_system(topic: str, domain: str, question: str) -> str:
    return f"""
You are a sophisticated research assistant.
Answer the following research question about topic '{topic}' in domain '{domain}':

{question}

Use search tool first when there is a need. Then accoriding to the result, answer the question.
""".strip()


def research_agent_user(topic: str, domain: str, question: str) -> str:
    return f"Research this question for topic={topic}, domain={domain}: {question}"


def report_agent_system(topic: str, domain: str, qa_sections: str) -> str:
    return f"""
You are a sophisticated research assistant. Compile the following research findings into a professional, McKinsey-style report.

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
You are an intent router for a deep-research web app. Read the user's message and session snapshot.
Classify intent and extract fields. Respond with ONE JSON object only, no markdown, no extra text.

Allowed intent values:
- full_research: user wants to start structured research (topic + domain).
- regenerate_questions: user wants new research questions; reuse session topic/domain if not restated.
- report_only: user only wants a final report from existing Q&A in session (topic/domain can match session).
- quick_answer: user wants a lighter answer; set need_web_search false unless they ask for sources.
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
- For full_research, fill topic and domain whenever inferable; otherwise intent=clarify.
- For off_topic, set reply_to_user politely; leave topic/domain empty.
""".strip()


def intent_router_user(user_text: str, session_context: dict) -> str:
    import json

    snap = json.dumps(session_context, ensure_ascii=False)
    return f"User message:\n{user_text}\n\nSession snapshot (JSON):\n{snap}"