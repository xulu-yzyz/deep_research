import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    deepseek_api_key: str
    deepseek_model_id: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    request_timeout_seconds: int = 80
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 8.0
    retry_jitter_seconds: float = 0.3

    redis_url: str = ""
    redis_enabled: bool = True
    redis_ttl_questions_seconds: int = 86400
    redis_ttl_answer_seconds: int = 86400
    tavily_api_key: str = ""

    # a2a_coordinator_port: int = 9999
    a2a_orchestrator_port: int = 10000
    a2a_question_port: int = 9997
    a2a_research_port: int = 9998
    a2a_report_port: int = 9999


def get_settings() -> Settings:
    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_model_id=os.getenv("DEEPSEEK_MODEL_ID", "deepseek-chat"),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        retry_base_delay_seconds=float(os.getenv("RETRY_BASE_DELAY_SECONDS", "1.0")),
        retry_max_delay_seconds=float(os.getenv("RETRY_MAX_DELAY_SECONDS", "8.0")),
        retry_jitter_seconds=float(os.getenv("RETRY_JITTER_SECONDS", "0.3")),
        redis_url=os.getenv("REDIS_URL", "").strip(),
        redis_enabled=os.getenv("REDIS_ENABLED", "1").strip().lower() not in ("0", "false", "no"),
        redis_ttl_questions_seconds=int(os.getenv("REDIS_TTL_QUESTIONS_SECONDS", "86400")),
        redis_ttl_answer_seconds=int(os.getenv("REDIS_TTL_ANSWER_SECONDS", "86400")),
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        # a2a_coordinator_port=int(os.getenv("A2A_COORDINATOR_PORT", "9999")),
        a2a_orchestrator_port=int(os.getenv("A2A_ORCHESTRATOR_PORT", "10001")),
        a2a_question_port=int(os.getenv("A2A_QUESTION_PORT", "9997")),
        a2a_research_port=int(os.getenv("A2A_RESEARCH_PORT", "9998")),
        a2a_report_port=int(os.getenv("A2A_REPORT_PORT", "9999")),
    )


def validate_required_keys(deepseek_api_key: str) -> tuple[bool, str]:
    if not deepseek_api_key:
        return False, "Please provide DEEPSEEK_API_KEY."
    return True, ""