from langchain_openai import ChatOpenAI


def build_llm(
    api_key: str,
    model_id: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com/v1",
) -> ChatOpenAI:
    """OpenAI-compatible chat model (DeepSeek API)."""
    return ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        temperature=0.2,
        timeout=120,
    )