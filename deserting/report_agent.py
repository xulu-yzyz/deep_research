from agno.agent import Agent


def build_report_agent(model, tools, topic: str, domain: str, qa_sections: str) -> Agent:
    return Agent(
        name="Report Compiler",
        model=model,
        tools=tools,
        instructions=f"""
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
""".strip(),
    )