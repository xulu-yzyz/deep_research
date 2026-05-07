import asyncio
import streamlit as st
from sqlalchemy.orm import Session

from app.cache.redis_client import get_redis_client
from app.config.settings import get_settings, validate_required_keys
from app.core import auth_service
from app.core import research_pipeline
from app.core.intent_router import route_user_message
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.core.stm_router_context import prepare_stm_router_context
from app.db.session import SessionLocal
from app.integrations.llm_client import build_llm
from app.db import research_repository

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
    st.session_state.setdefault("bypass_questions_cache", False)
    st.session_state.setdefault("pipeline_last_error", "")
    st.session_state.setdefault("use_a2a_orchestrator", False)
    st.session_state.setdefault(
        "a2a_base_url", f"http://127.0.0.1:{settings.a2a_orchestrator_port}"
    )
    st.session_state.setdefault("chat_turns", [])
    st.session_state.setdefault("stm_dialogue_summary", "")

    # New staged workflow states
    st.session_state.setdefault("topic_domain_confirmed", False)
    st.session_state.setdefault("questions_confirmed", False)
    st.session_state.setdefault("answers_confirmed", False)


async def _call_orchestrator(base_url: str, payload: dict) -> dict:
    return await send_json_message(base_url.rstrip("/"), payload)


def _run_orchestrator_via_a2a_sync(base_url: str, payload: dict) -> dict:
    return asyncio.run(_call_orchestrator(base_url, payload))


def _reset_after_topic_change() -> None:
    st.session_state["questions"] = []
    st.session_state["research_question_ids"] = []
    st.session_state["question_answers"] = []
    st.session_state["report_content"] = ""
    st.session_state["research_complete"] = False
    st.session_state["research_run_id"] = None
    st.session_state["questions_confirmed"] = False
    st.session_state["answers_confirmed"] = False


def _reset_after_questions_change() -> None:
    st.session_state["question_answers"] = []
    st.session_state["report_content"] = ""
    st.session_state["research_complete"] = False
    st.session_state["answers_confirmed"] = False


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
                "bypass_questions_cache",
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

    # Workflow recovery flags
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
            f"[{r.status}] {r.topic}  /  {r.domain}  (run_id={r.run_id})": r.run_id
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
    # Apply pending from router before widgets bind.
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
    redis_ok = get_redis_client() is not None
    st.sidebar.caption(f"Redis cache: {'connected' if redis_ok else 'off / unavailable'}")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### About")
    st.sidebar.info(
        "Step-by-step mode: confirm topic/domain -> confirm questions -> generate answers -> confirm answers -> generate report."
    )

    st.title("🔍 AI DeepResearch Agent")
    st.caption("分步确认模式（非一键执行）")

    valid, err_msg = validate_required_keys(deepseek_api_key)
    if not valid:
        st.warning(f"⚠️ {err_msg}")
        return

    llm = build_llm(
        api_key=deepseek_api_key,
        model_id=settings.deepseek_model_id,
        base_url=settings.deepseek_base_url,
    )

    uid = int(st.session_state["user_id"])
    _render_history_restore_panel(uid)

    st.header("Step 0: 输入需求")
    st.text_area(
        "用自然语言描述需求",
        key="user_request",
        height=120,
        placeholder="例如：请调研美国关税对半导体供应链影响，领域为国际贸易与产业政策。",
    )

    if st.button("Step 1: 解析需求并提取 topic/domain", type="primary", key="parse_request"):
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

            with st.spinner("意图路由中..."):
                decision, _ = route_user_message(llm, raw, ctx, retry_policy, pipeline_metrics)

            st.session_state["routed_intent"] = decision.intent
            st.session_state["use_web_search"] = decision.need_web_search
            st.session_state["last_router_message"] = decision.reply_to_user or ""

            if decision.intent == "off_topic":
                st.info(decision.reply_to_user or "当前需求与深度调研无关，请重新输入。")
            elif decision.intent == "clarify":
                st.warning(
                    decision.clarify_prompt
                    or decision.reply_to_user
                    or "信息不足，请补充主题与领域。"
                )
            else:
                if decision.topic:
                    st.session_state["research_topic"] = decision.topic.strip()
                if decision.domain:
                    st.session_state["research_domain"] = decision.domain.strip()
                st.success("已提取 topic/domain，请在下一步确认。")
                _append_stm_turn(
                    "assistant",
                    f"提取结果：topic={st.session_state.get('research_topic','')}, domain={st.session_state.get('research_domain','')}",
                )

            # Any new parse invalidates downstream confirmations
            st.session_state["topic_domain_confirmed"] = False
            _reset_after_topic_change()

    st.header("Step 2: 确认 topic / domain")
    c1, c2 = st.columns(2)
    with c1:
        old_topic = st.session_state.get("research_topic", "")
        st.text_input("Research topic", key="research_topic")
        if old_topic != st.session_state.get("research_topic", ""):
            st.session_state["topic_domain_confirmed"] = False
            _reset_after_topic_change()
    with c2:
        old_domain = st.session_state.get("research_domain", "")
        st.text_input("Domain", key="research_domain")
        if old_domain != st.session_state.get("research_domain", ""):
            st.session_state["topic_domain_confirmed"] = False
            _reset_after_topic_change()

    c3, c4 = st.columns(2)
    with c3:
        if st.button("确认 topic/domain", key="confirm_topic_domain"):
            topic = (st.session_state.get("research_topic") or "").strip()
            domain = (st.session_state.get("research_domain") or "").strip()
            if not topic or not domain:
                st.error("请先填写完整 topic 和 domain。")
            else:
                st.session_state["topic_domain_confirmed"] = True
                st.success("已确认 topic/domain。")
    with c4:
        if st.button("重置后续阶段", key="reset_downstream"):
            _reset_after_topic_change()
            st.session_state["topic_domain_confirmed"] = False
            st.info("已重置问题、答案和报告。")

    if not st.session_state.get("topic_domain_confirmed"):
        st.info("请先确认 topic/domain，之后才能生成调查问题。")
        return

    st.header("Step 3: 生成并确认调查问题")
    if st.button("生成调查问题", key="gen_questions_btn"):
        db = _db()
        topic = (st.session_state.get("research_topic") or "").strip()
        domain = (st.session_state.get("research_domain") or "").strip()
        force_new = True
        old_qids = [int(x) for x in (st.session_state.get("research_question_ids") or [])]
        try:
            with st.spinner("生成问题中..."):
                qres = research_pipeline.run_questions_phase(
                    db,
                    uid,
                    topic,
                    domain,
                    force_new=force_new,
                    llm=llm,
                    settings=settings,
                    retry_policy=retry_policy,
                    metrics=pipeline_metrics,
                    old_question_ids=old_qids,
                )
            if len(qres) == 4:
                st.error(f"生成问题失败：{qres[3]}")
            else:
                questions, q_ids, run_id = qres[0], qres[1], qres[2]
                st.session_state["questions"] = questions
                st.session_state["research_question_ids"] = q_ids
                st.session_state["research_run_id"] = run_id
                st.session_state["questions_confirmed"] = False
                _reset_after_questions_change()
                st.success("问题已生成，请确认后再生成答案。")
        finally:
            db.close()

    if st.session_state.get("questions"):
        for i, q in enumerate(st.session_state["questions"], 1):
            st.markdown(f"**{i}. {q}**")

        c5, c6 = st.columns(2)
        with c5:
            if st.button("确认这些问题", key="confirm_questions_btn"):
                st.session_state["questions_confirmed"] = True
                st.success("问题已确认。")
        with c6:
            if st.button("重新生成问题", key="regen_questions_btn"):
                st.session_state["questions_confirmed"] = False
                _reset_after_questions_change()
                st.info("请点击“生成调查问题”重新生成。")
    else:
        st.info("尚未生成问题。")

    if not st.session_state.get("questions_confirmed"):
        return

    st.header("Step 4: 用户同意后生成答案")
    if st.button("生成答案（Research）", type="primary", key="run_research_btn"):
        db = _db()
        try:
            topic = (st.session_state.get("research_topic") or "").strip()
            domain = (st.session_state.get("research_domain") or "").strip()
            questions = list(st.session_state.get("questions") or [])
            q_ids = [int(x) for x in (st.session_state.get("research_question_ids") or [])]
            run_id = st.session_state.get("research_run_id")

            if not questions or not q_ids or not run_id:
                st.error("缺少问题或 run_id，无法执行调研。")
            else:
                tavily_effective = (
                    (tavily_key or None)
                    if st.session_state.get("use_web_search", True)
                    else None
                )
                with st.spinner("调研并生成答案中..."):
                    question_answers, err = research_pipeline.run_research_phase(
                        db,
                        llm=llm,
                        topic=topic,
                        domain=domain,
                        questions=questions,
                        q_ids=q_ids,
                        run_id=int(run_id),
                        retry_policy=retry_policy,
                        metrics=pipeline_metrics,
                        tavily_api_key=tavily_effective,
                        settings=settings,
                    )
                if err:
                    st.error(f"生成答案失败：{err}")
                else:
                    st.session_state["question_answers"] = question_answers
                    st.session_state["answers_confirmed"] = False
                    st.session_state["report_content"] = ""
                    st.session_state["research_complete"] = False
                    st.success("答案已生成，请检查后确认。")
        finally:
            db.close()

    if st.session_state.get("question_answers"):
        st.subheader("Research Results")
        for i, qa in enumerate(st.session_state["question_answers"], 1):
            st.markdown(f"### Question {i}")
            st.markdown(f"**{qa.get('question','')}**")
            st.markdown(qa.get("answer", ""))

        c7, c8 = st.columns(2)
        with c7:
            if st.button("答案无误，确认进入报告阶段", key="confirm_answers_btn"):
                st.session_state["answers_confirmed"] = True
                st.success("已确认答案。")
        with c8:
            if st.button("答案需重做", key="reject_answers_btn"):
                st.session_state["answers_confirmed"] = False
                st.session_state["report_content"] = ""
                st.session_state["research_complete"] = False
                st.info("请调整后重新点击“生成答案（Research）”。")

    if not st.session_state.get("answers_confirmed"):
        return

    st.header("Step 5: 生成最终报告")
    if st.button("生成报告", type="primary", key="gen_report_btn"):
        db = _db()
        try:
            run_id = st.session_state.get("research_run_id")
            topic = (st.session_state.get("research_topic") or "").strip()
            domain = (st.session_state.get("research_domain") or "").strip()
            qa = list(st.session_state.get("question_answers") or [])

            if not run_id:
                st.error("缺少 run_id，无法写入报告。")
            elif not qa:
                st.error("没有可用于报告的 Q&A。")
            else:
                with st.spinner("生成报告中..."):
                    report, err = research_pipeline.run_report_phase(
                        llm,
                        topic,
                        domain,
                        qa,
                        retry_policy,
                        pipeline_metrics,
                    )
                if err:
                    st.error(f"生成报告失败：{err}")
                else:
                    try:
                        research_repository.upsert_report(
                            db,
                            int(run_id),
                            str(report),
                            format="markdown",
                        )
                        db.commit()
                    except Exception as e:
                        st.error(f"报告写入数据库失败：{e}")
                        return

                    st.session_state["report_content"] = report
                    st.session_state["research_complete"] = True
                    st.success("报告已生成。")
        finally:
            db.close()

    if st.session_state.get("research_complete") and st.session_state.get("report_content"):
        st.header("Final Report")
        with st.expander("View Full Report Content", expanded=True):
            st.markdown(st.session_state["report_content"])

    if st.session_state.get("pipeline_last_error"):
        with st.expander("上次流水线错误", expanded=False):
            st.code(st.session_state.pipeline_last_error)

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