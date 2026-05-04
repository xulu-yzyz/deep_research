from agno.agent import Agent


def build_question_generator(model) -> Agent:
    return Agent(
        name="Question Generator",
        model=model,
        instructions="""
You are an expert at breaking down research topics into specific questions.
Generate exactly 5 specific yes/no research questions about the given topic in the specified domain.
Respond ONLY with the text of the 5 questions formatted as a numbered list, and NOTHING ELSE.
""".strip(),
    )