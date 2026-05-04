# app/integrations/tavily_search.py
from __future__ import annotations

from langchain_tavily import TavilySearch


def build_tavily_search_tool(
    *,
    max_results: int = 2,
    include_answer: bool = True,
    search_depth: str = "basic",
) -> TavilySearch:
    """
    构造 Tavily 搜索工具。API Key 从环境变量 TAVILY_API_KEY 读取
    （langchain-tavily / 底层客户端会读该变量；也可在 TavilySearch(api_key=...) 显式传入）。
    """
    return TavilySearch(
        max_results=max_results,
        topic="general",
        include_answer=include_answer,
        search_depth=search_depth,
        # include_raw_content=True  # 更长、更耗 token，按需打开
    )