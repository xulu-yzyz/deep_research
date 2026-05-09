# app/integrations/research_agent_run.py
from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, ToolMessage


def _log_agent_messages(messages: list) -> None:
    for i, m in enumerate(messages):
        cls = type(m).__name__
        print(f"[agent step {i}] {cls}")
        if isinstance(m, AIMessage):
            calls = getattr(m, "tool_calls", None) or []
            if calls:
                for tc in calls:
                    name = tc.get("name", tc.get("function", {}).get("name"))
                    args = tc.get("args") or tc.get("arguments")
                    print(f"    → tool_call: {name!r} args={args!r}")
            else:
                # 纯文本回复（可能是最终答案，也可能是中间轮）
                c = (m.content or "")[:200]
                if c:
                    print(f"    text preview: {c!r}...")
        if isinstance(m, ToolMessage):
            name = getattr(m, "name", "?")
            body = m.content or ""
            preview = body[:300] + ("..." if len(body) > 300 else "")
            print(f"    ← 工具调用: {name!r} 预览={preview!r}")


def run_research_with_tools(
    llm: ChatOpenAI,
    tools: list[BaseTool],
    system_prompt: str,
    user_prompt: str,
) -> str:
    """
    用绑定工具的 agent 执行调研：模型可多次调用 Tavily，再输出最终自然语言答案。
    """
    agent = create_agent(llm, tools)

    # 把系统指令放进首条 user 或按你用的模板合并；最简做法是把 system 拼进 user 前部
    combined = f"{system_prompt}\n\n---\n\n{user_prompt}"

    result = agent.invoke({"messages": [("user", combined)]})
    msgs = result.get("messages", [])
    _log_agent_messages(msgs)
    
    messages = result.get("messages", [])
    if not messages:
        return ""

    last = messages[-1]
    content = getattr(last, "content", None)
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()