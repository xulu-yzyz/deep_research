import asyncio
import streamlit as st
from sqlalchemy.orm import Session

from app.cache.redis_client import get_redis_client
from app.config.settings import get_settings, validate_required_keys
from app.core import auth_service
from app.core.intent_router import route_user_message
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.core.stm_router_context import prepare_stm_router_context
from app.db import research_repository
from app.db.session import SessionLocal
from app.integrations.llm_client import build_llm
from app.core.working_memory import empty_working_memory
import re
import subprocess
import sys
from pathlib import Path
try:
    import httpx  # noqa: F401
    from app.a2a.a2a_json_client import send_json_message

    _A2A_AVAILABLE = True
except ImportError:
    _A2A_AVAILABLE = False


settings = get_settings()
_STM_HARD_MAX_TURNS = settings._STM_HARD_MAX_TURNS


def _db() -> Session:
    return SessionLocal()


def _trim_stm_turns(turns: list) -> None:
    while len(turns) > _STM_HARD_MAX_TURNS:
        turns.pop(0)


def _append_stm_turn(role: str, content: str) -> None:
    c = (content or "").strip()
    if not c:
        return
    turns: list = st.session_state.setdefault("chat_turns", [])
    turns.append({"role": role, "content": c})
    _trim_stm_turns(turns)


def _reset_after_topic_change() -> None:
    st.session_state["questions"] = []
    st.session_state["question_answers"] = []
    st.session_state["report_content"] = ""
    st.session_state["research_complete"] = False
    st.session_state["research_run_id"] = None
    st.session_state["research_question_ids"] = []
    st.session_state["questions_confirmed"] = False
    st.session_state["answers_confirmed"] = False


def _reset_after_questions_change() -> None:
    st.session_state["question_answers"] = []
    st.session_state["report_content"] = ""
    st.session_state["research_complete"] = False
    st.session_state["answers_confirmed"] = False


def init_session_state() -> None:
    if "user_id" not in st.session_state:
        st.session_state.user_id = None
    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    if "user_display_name" not in st.session_state:
        st.session_state.user_display_name = None

    st.session_state.setdefault("questions", [])
    st.session_state.setdefault("question_answers", [])
    st.session_state.setdefault("report_content", "")
    st.session_state.setdefault("research_complete", False)
    st.session_state.setdefault("research_run_id", None)
    st.session_state.setdefault("research_question_ids", [])

    st.session_state.setdefault("user_request", "")
    st.session_state.setdefault("research_topic", "")
    st.session_state.setdefault("research_domain", "")
    st.session_state.setdefault("use_web_search", True)
    st.session_state.setdefault("routed_intent", "")
    st.session_state.setdefault("last_router_message", "")
    st.session_state.setdefault("pipeline_last_error", "")
    st.session_state.setdefault("chat_turns", [])
    st.session_state.setdefault("stm_dialogue_summary", "")

    st.session_state.setdefault("use_a2a_orchestrator", True)
    st.session_state.setdefault(
        "a2a_base_url", f"http://127.0.0.1:{settings.a2a_orchestrator_port}"
    )

    # Strict staged confirmation flags
    st.session_state.setdefault("topic_domain_confirmed", False)
    st.session_state.setdefault("questions_confirmed", False)
    st.session_state.setdefault("answers_confirmed", False)

    st.session_state.setdefault("agent_working_memory", empty_working_memory())

    st.session_state.setdefault("rag_enabled", False)
    st.session_state.setdefault("rag_persist_directory", ".rag/chroma")
    st.session_state.setdefault("rag_collection_name", "research_docs")
    st.session_state.setdefault("rag_docs_dir", "docs/rag")
    st.session_state.setdefault("rag_last_index_output", "")


async def _call_orchestrator(base_url: str, payload: dict) -> dict:
    return await send_json_message(base_url.rstrip("/"), payload)


def _run_orchestrator_via_a2a_sync(base_url: str, payload: dict) -> dict:
    return asyncio.run(_call_orchestrator(base_url, payload))


def _invoke_orchestrator(base_url: str, payload: dict, spinner_text: str) -> dict:
    if not _A2A_AVAILABLE:
        return {"error": 'A2A not available. Install: pip install "a2a-sdk[http-server]" httpx'}
    if not (base_url or "").strip():
        return {"error": "A2A Base URL is empty."}
    with st.spinner(spinner_text):
        return _run_orchestrator_via_a2a_sync(base_url, payload)

def _safe_slug(value: str, fallback: str = "default") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip()).strip("_")
    return slug or fallback


def _rag_paths_for_user(uid: int, domain: str) -> tuple[str, str, str]:
    domain_slug = _safe_slug(domain, "general")
    docs_dir = f"docs/rag/{uid}/{domain_slug}"
    persist_dir = ".rag/chroma"
    collection = f"research_docs_{uid}_{domain_slug}"
    return docs_dir, persist_dir, collection


def _save_uploaded_rag_files(uploaded_files: list, docs_dir: str) -> list[str]:
    target_dir = Path(docs_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for file in uploaded_files:
        safe_name = _safe_slug(Path(file.name).stem, "document") + Path(file.name).suffix.lower()
        target_path = target_dir / safe_name
        target_path.write_bytes(file.getbuffer())
        saved.append(str(target_path))

    return saved


def _index_rag_documents(docs_dir: str, persist_dir: str, collection: str, domain: str) -> tuple[bool, str]:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "app" / "rag" / "index_rag_documents.py"

    cmd = [
        sys.executable,
        str(script_path),
        "--docs-dir",
        docs_dir,
        "--persist-dir",
        persist_dir,
        "--collection",
        collection,
        "--domain",
        domain,
    ]

    result = subprocess.run(
        cmd,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=180,
    )

    output = "\n".join(x for x in [result.stdout, result.stderr] if x.strip()).strip()
    return result.returncode == 0, output


def _sync_stage_result_to_session(data: dict) -> None:
    err = data.get("error")
    st.session_state["pipeline_last_error"] = str(err or "")

    if err:
        return

    if "questions" in data:
        st.session_state["questions"] = list(data.get("questions") or [])
    if "question_ids" in data:
        st.session_state["research_question_ids"] = [
            int(x) for x in (data.get("question_ids") or [])
        ]
    if "run_id" in data and data.get("run_id") is not None:
        st.session_state["research_run_id"] = int(data["run_id"])
    if "question_answers" in data:
        st.session_state["question_answers"] = list(data.get("question_answers") or [])
    if "report" in data:
        st.session_state["report_content"] = str(data.get("report") or "")
    if "research_complete" in data:
        st.session_state["research_complete"] = bool(data.get("research_complete"))

    if "working_memory" in data:
            st.session_state["agent_working_memory"] = data["working_memory"]


def render_auth_sidebar() -> None:
    st.sidebar.markdown("### Account")
    if st.session_state.get("user_id") is not None:
        label = st.session_state.get("user_display_name") or st.session_state.get(
            "user_email"
        )
        st.sidebar.write(f"Signed in as **{label}**")
        if st.sidebar.button("Log out", key="logout_btn"):
            for k in ("user_id", "user_email", "user_display_name"):
                st.session_state.pop(k, None)
            for k in (
                "questions",
                "question_answers",
                "report_content",
                "research_complete",
                "research_run_id",
                "research_question_ids",
                "chat_turns",
                "user_request",
                "research_topic",
                "research_domain",
                "_pending_research_topic",
                "_pending_research_domain",
                "use_web_search",
                "routed_intent",
                "last_router_message",
                "pipeline_last_error",
                "use_a2a_orchestrator",
                "a2a_base_url",
                "stm_dialogue_summary",
                "topic_domain_confirmed",
                "questions_confirmed",
                "answers_confirmed",
            ):
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.sidebar.info("Please sign in to continue.")


def render_login_register() -> None:
    st.title("Sign in")
    tab_login, tab_reg = st.tabs(["Login", "Register"])

    with tab_login:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Login", key="login_btn"):
            db = _db()
            try:
                user = auth_service.authenticate(db, email, password)
                if not user:
                    st.error("Invalid email or password.")
                else:
                    st.session_state["user_id"] = int(user.id)
                    st.session_state["user_email"] = user.email
                    st.session_state["user_display_name"] = user.display_name
                    st.success("Logged in.")
                    st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")
            finally:
                db.close()

    with tab_reg:
        email_r = st.text_input("Email", key="reg_email")
        pw_r = st.text_input("Password", type="password", key="reg_pw")
        pw2 = st.text_input("Confirm password", type="password", key="reg_pw2")
        name = st.text_input("Display name (optional)", key="reg_name")
        if st.button("Create account", key="reg_btn"):
            if pw_r != pw2:
                st.error("Passwords do not match.")
            elif len(pw_r) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                db = _db()
                try:
                    user = auth_service.register_user(db, email_r, pw_r, name or None)
                    st.session_state["user_id"] = int(user.id)
                    st.session_state["user_email"] = user.email
                    st.session_state["user_display_name"] = user.display_name
                    st.success("Account created. You are logged in.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Registration failed: {e}")
                finally:
                    db.close()


def _restore_run_into_session(db: Session, uid: int, run_id: int) -> None:
    bundle = research_repository.load_run_bundle(db, uid, run_id)
    st.session_state["research_run_id"] = int(bundle["run_id"])
    st.session_state["research_topic"] = str(bundle["topic"])
    st.session_state["research_domain"] = str(bundle["domain"])
    st.session_state["questions"] = list(bundle.get("questions") or [])
    st.session_state["research_question_ids"] = [
        int(x) for x in (bundle.get("question_ids") or [])
    ]
    st.session_state["question_answers"] = list(bundle.get("question_answers") or [])
    st.session_state["report_content"] = str(bundle.get("report") or "")
    st.session_state["research_complete"] = bool(st.session_state["report_content"])

    st.session_state["topic_domain_confirmed"] = True
    st.session_state["questions_confirmed"] = bool(st.session_state["questions"])
    st.session_state["answers_confirmed"] = bool(st.session_state["question_answers"])


def _render_history_restore_panel(uid: int) -> None:
    db = _db()
    try:
        runs = research_repository.list_recent_runs(db, uid, limit=20)
    finally:
        db.close()

    if not runs:
        return

    with st.expander("🧠 恢复历史调研会话", expanded=False):
        options = {
            f"[{r.status}] {r.topic} / {r.domain} (run_id={r.run_id})": r.run_id
            for r in runs
        }
        label = st.selectbox("选择一个历史会话", list(options.keys()))
        picked_run_id = int(options[label])

        c1, c2 = st.columns(2)
        with c1:
            if st.button("仅回填主题/领域", key="prefill_topic_domain"):
                db = _db()
                try:
                    bundle = research_repository.load_run_bundle(db, uid, picked_run_id)
                finally:
                    db.close()
                st.session_state["research_topic"] = str(bundle["topic"])
                st.session_state["research_domain"] = str(bundle["domain"])
                st.session_state["topic_domain_confirmed"] = False
                _reset_after_topic_change()
                st.rerun()

        with c2:
            if st.button("恢复该会话（含报告）", type="primary", key="restore_full_run"):
                db = _db()
                try:
                    _restore_run_into_session(db, uid, picked_run_id)
                finally:
                    db.close()
                st.success("已恢复：主题/领域、问题、答案与报告。")
                st.rerun()


def render_research_app() -> None:
    if "_pending_research_topic" in st.session_state:
        st.session_state["research_topic"] = st.session_state.pop("_pending_research_topic")
    if "_pending_research_domain" in st.session_state:
        st.session_state["research_domain"] = st.session_state.pop("_pending_research_domain")

    retry_policy = RetryPolicy(
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
        jitter_seconds=settings.retry_jitter_seconds,
    )
    pipeline_metrics = PipelineMetrics()

    st.sidebar.header("⚙️ Configuration")
    deepseek_api_key = st.sidebar.text_input(
        "DEEPSEEK_API_KEY",
        value=settings.deepseek_api_key,
        type="password",
    )
    tavily_key = st.sidebar.text_input(
        "TAVILY_API_KEY (optional)",
        value=settings.tavily_api_key,
        type="password",
    )
    uid = int(st.session_state["user_id"])
    st.sidebar.markdown("---")
    st.sidebar.markdown("### RAG Knowledge Base")

    current_domain = st.session_state.get("research_domain", "")
    rag_docs_dir, rag_persist_dir, rag_collection = _rag_paths_for_user(uid, current_domain)

    st.session_state["rag_docs_dir"] = rag_docs_dir
    st.session_state["rag_persist_directory"] = rag_persist_dir
    st.session_state["rag_collection_name"] = rag_collection

    st.sidebar.checkbox("Enable RAG", key="rag_enabled")

    uploaded_rag_files = st.sidebar.file_uploader(
        "Upload RAG documents",
        type=["pdf", "txt", "md"],
        accept_multiple_files=True,
        help="上传后点击 Build / Update RAG Index，文档会被切块并写入本地 Chroma 向量库。",
    )

    st.sidebar.caption(f"Docs dir: `{rag_docs_dir}`")
    st.sidebar.caption(f"Collection: `{rag_collection}`")

    if st.sidebar.button("Build / Update RAG Index", key="build_rag_index"):
        if not uploaded_rag_files:
            st.sidebar.warning("请先上传 PDF / TXT / MD 文件。")
        else:
            try:
                saved = _save_uploaded_rag_files(uploaded_rag_files, rag_docs_dir)
                ok, output = _index_rag_documents(
                    docs_dir=rag_docs_dir,
                    persist_dir=rag_persist_dir,
                    collection=rag_collection,
                    domain=current_domain or "general",
                )
                st.session_state["rag_last_index_output"] = output

                if ok:
                    st.sidebar.success(f"RAG index built. Saved {len(saved)} file(s).")
                    
                else:
                    st.sidebar.error("RAG indexing failed.")
            except Exception as e:
                st.session_state["rag_last_index_output"] = str(e)
                st.sidebar.error(f"RAG indexing failed: {e}")

    if st.session_state.get("rag_last_index_output"):
        with st.sidebar.expander("RAG indexing output", expanded=False):
            st.code(st.session_state["rag_last_index_output"])

    redis_ok = get_redis_client() is not None
    st.sidebar.caption(f"Redis cache: {'connected' if redis_ok else 'off / unavailable'}")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### A2A Orchestrator")
    if not _A2A_AVAILABLE:
        st.sidebar.warning('未安装依赖。请安装: pip install "a2a-sdk[http-server]" httpx')
    st.sidebar.text_input(
        "Orchestrator Base URL",
        key="a2a_base_url",
        value=f"http://127.0.0.1:{settings.a2a_orchestrator_port}",
        help="严格 A2A 模式：所有执行阶段都通过 orchestrator",
    )

    st.title("🔍 AI DeepResearch Agent (Strict A2A)")
    st.caption("流程：需求 -> 确认 topic/domain -> 确认问题 -> 生成答案 -> 确认答案 -> 生成报告")

    valid, err_msg = validate_required_keys(deepseek_api_key)
    if not valid:
        st.warning(f"⚠️ {err_msg}")
        return

    llm = build_llm(
        api_key=deepseek_api_key,
        model_id=settings.deepseek_model_id,
        base_url=settings.deepseek_base_url,
    )

    
    _render_history_restore_panel(uid)

    st.header("Step 0: 输入需求")
    st.text_area(
        "用自然语言描述你的需求",
        key="user_request",
        height=120,
        placeholder="例如：请调研美国关税对半导体供应链影响，领域是国际贸易与产业政策。",
    )

    if st.button("Step 1: 解析需求（仅路由，不执行）", type="primary", key="parse_req"):
        raw = (st.session_state.get("user_request") or "").strip()
        if not raw:
            st.warning("请先输入需求描述。")
        else:
            _append_stm_turn("user", raw)
            turns: list = st.session_state.setdefault("chat_turns", [])
            summ = str(st.session_state.get("stm_dialogue_summary") or "")
            base_snap = {
                "research_topic": (st.session_state.get("research_topic") or "").strip(),
                "research_domain": (st.session_state.get("research_domain") or "").strip(),
                "has_questions": bool(st.session_state.get("questions")),
                "has_answers": bool(st.session_state.get("question_answers")),
            }
            ctx, summ2 = prepare_stm_router_context(
                llm=llm,
                user_text=raw,
                base_snapshot=base_snap,
                chat_turns=turns,
                dialogue_summary=summ,
                settings=settings,
                retry_policy=retry_policy,
                metrics=pipeline_metrics,
            )
            st.session_state["stm_dialogue_summary"] = summ2
            decision, _ = route_user_message(llm, raw, ctx, retry_policy, pipeline_metrics)
            assistant_summary = (
                f"intent={decision.intent}; "
                f"topic={decision.topic}; "
                f"domain={decision.domain}; "
                f"message={decision.reply_to_user or decision.clarify_prompt}"
            )
            _append_stm_turn("assistant", assistant_summary)
            st.session_state["routed_intent"] = decision.intent
            st.session_state["use_web_search"] = decision.need_web_search
            st.session_state["last_router_message"] = decision.reply_to_user or ""

            if decision.intent == "off_topic":
                st.info(decision.reply_to_user or "当前输入不属于调研请求，请重试。")
            elif decision.intent == "clarify":
                st.warning(
                    decision.clarify_prompt
                    or decision.reply_to_user
                    or "信息不足，请补充主题与领域。"
                )
            else:
                if (decision.topic or "").strip():
                    st.session_state["research_topic"] = decision.topic.strip()
                if (decision.domain or "").strip():
                    st.session_state["research_domain"] = decision.domain.strip()
                st.success("已提取 topic/domain，请确认。")

            st.session_state["topic_domain_confirmed"] = False
            _reset_after_topic_change()

    st.header("Step 2: 确认 Topic / Domain")
    topic_before = st.session_state.get("research_topic", "")
    domain_before = st.session_state.get("research_domain", "")
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("Research topic", key="research_topic")
    with c2:
        st.text_input("Domain", key="research_domain")

    if topic_before != st.session_state.get("research_topic", "") or domain_before != st.session_state.get("research_domain", ""):
        st.session_state["topic_domain_confirmed"] = False
        _reset_after_topic_change()

    c3, c4 = st.columns(2)
    with c3:
        if st.button("确认 topic/domain", key="confirm_td"):
            if not (st.session_state.get("research_topic") or "").strip():
                st.error("Research topic 不能为空。")
            elif not (st.session_state.get("research_domain") or "").strip():
                st.error("Domain 不能为空。")
            else:
                st.session_state["topic_domain_confirmed"] = True
                st.success("已确认 topic/domain。")
    with c4:
        if st.button("清空后续阶段", key="clear_downstream"):
            st.session_state["topic_domain_confirmed"] = False
            _reset_after_topic_change()
            st.info("已清空问题、答案、报告。")

    if not st.session_state.get("topic_domain_confirmed"):
        st.info("请先确认 topic/domain。")
        return
    st.header("Step 3: Agentic Deep Research")

    if st.button("开始智能调研", type="primary", key="run_agentic_research"):
        payload = {
            "uid": uid,
            "topic": (st.session_state.get("research_topic") or "").strip(),
            "domain": (st.session_state.get("research_domain") or "").strip(),
            "stages": ["agentic_research", "memory_commit"],
            # "tavily_api_key": (tavily_key or "").strip() or None,
            "tavily_api_key": None,
            "user_request": st.session_state.get("user_request", ""),
            "working_memory": st.session_state.get("agent_working_memory") or {},
            "rag_enabled": bool(st.session_state.get("rag_enabled")),
            "rag_persist_directory": st.session_state.get("rag_persist_directory") or ".rag/chroma",
            "rag_collection_name": st.session_state.get("rag_collection_name") or "research_docs",
        }
        data = _invoke_orchestrator(
            st.session_state.get("a2a_base_url", ""),
            payload,
            "Agent 正在规划、调研、反思并生成报告...",
        )
        
        _sync_stage_result_to_session(data)
    
    

    if st.session_state.get("research_complete") and st.session_state.get("report_content"):
        st.header("Final Report")
        with st.expander("View Full Report Content", expanded=True):
            st.markdown(st.session_state["report_content"])

    if st.session_state.get("pipeline_last_error"):
        with st.expander("上次错误", expanded=False):
            st.code(st.session_state["pipeline_last_error"])

    if st.session_state.get("routed_intent") or st.session_state.get("last_router_message"):
        with st.expander("上次路由摘要", expanded=False):
            st.write(f"intent: {st.session_state.get('routed_intent', '')}")
            st.write(st.session_state.get("last_router_message", ""))


def render_streamlit_app() -> None:
    st.set_page_config(
        page_title="AI DeepResearch Agent",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_session_state()

    if st.session_state.get("user_id") is None:
        render_login_register()
        return

    render_auth_sidebar()
    render_research_app()


if __name__ == "__main__":
    render_streamlit_app()