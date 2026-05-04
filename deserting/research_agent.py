from agno.agent import Agent


def build_research_agent(model, tools, topic: str, domain: str, question: str) -> Agent:
    return Agent(
        name="Research Agent",
        model=model,
        tools=tools,
        instructions=f"""
You are a sophisticated research assistant.
Answer the following research question about topic '{topic}' in domain '{domain}':

{question}

Use available search tools and provide a concise, well-sourced answer.
""".strip(),
    )