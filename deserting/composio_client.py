import os

from composio import Composio
from composio_langchain import LangchainProvider


def build_composio_tools(composio_api_key: str, composio_user_id: str | int):
    """
    LangChain-native tools from Composio (v3 SDK).

    composio_user_id: stable per-app user (e.g. Streamlit user id) for Composio sessions.
    """
    os.environ["COMPOSIO_API_KEY"] = composio_api_key.strip()
    composio = Composio(provider=LangchainProvider())
    uid = str(composio_user_id)
    session = composio.create(
        user_id=uid,
        tools={"composio_search": ["COMPOSIO_SEARCH_TAVILY_SEARCH"]},
        workbench={"enable": False},
    )
    tools = session.tools()
    return list(tools) if tools else []