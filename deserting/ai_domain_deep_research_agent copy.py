import os
import asyncio
import streamlit as st
from dotenv import load_dotenv
from agno.agent import Agent
from agno.run.agent import RunOutput
from composio_agno import ComposioToolSet, Action
from agno.models.deepseek import DeepSeek


load_dotenv()

def initialize_agent(DEEPSEEK_API_KEY,composio_api_key):
    llm=DeepSeek(id="deepseek-chat,api_key=DEEPSEEK_API_KEY",base_url="https://api.deepseek.com/v1")
    toolset=ComposioToolSet(api_key=composio_key)
    composio_tools=toolset.get_tools(action=[Action.COMPOSIO_SEARCH_TAVILY_SEARCH])
    return llm,composio_tools

if DEEPSEEK_API_KEY and composio_api_key:

    llm,composio_tools=initialize_agent(DEEPSEEK_API_KEY,composio_api_key);
    