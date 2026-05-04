import streamlit as st
from sqlalchemy.orm import Session

from app.cache import research_redis_cache
from app.cache.redis_client import get_redis_client
from app.config.settings import get_settings, validate_required_keys
from app.core import auth_service
from app.core.intent_router import route_user_message
from app.core.research_service import compile_report, generate_questions, research_one_question
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.db import research_repository
from app.db.session import SessionLocal
from app.integrations.llm_client import build_llm


def _db() -> Session:
    return SessionLocal()


def init_session_state() -> None:
    if "user_id" not in st.session_state:
        st.session_state.user_id = None
    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    if "user_display_name" not in st.session_state:
        st.session_state.user_display_name = None
    if "questions" not in st.session_state:
        st.session_state.questions = []
    if "question_answers" not in st.session_state:
        st.session_state.question_answers = []
    if "report_content" not in st.session_state:
        st.session_state.report_content = ""
    if "research_complete" not in st.session_state:
        st.session_state.research_complete = False
    if "research_run_id" not in st.session_state:
        st.session_state.research_run_id = None
    if "research_question_ids" not in st.session_state:
        st.session_state.research_question_ids = []
    st.session_state.setdefault("user_request", "")
    st.session_state.setdefault("research_topic", "")
    st.session_state.setdefault("research_domain", "")
    st.session_state.setdefault("use_web_search", True)
    st.session_state.setdefault("routed_intent", "")
    st.session_state.setdefault("last_router_message", "")
    st.session_state.setdefault("bypass_questions_cache", False)


def render_auth_sidebar() -> None:
    st.sidebar.markdown("### Account")
    if st.session_state.get("user_id") is not None:
        label = st.session_state.get("user_display_name") or st.session_state.get("user_email")
        st.sidebar.write(f"Signed in as **{label}**")
        if st.sidebar.button("Log out", key="logout_btn"):
            for k in ("user_id", "user_email", "user_display_name"):
                st.session_state.pop(k, None)
            st.session_state.questions = []
            st.session_state.question_answers = []
            st.session_state.report_content = ""
            st.session_state.research_complete = False
            st.session_state.research_run_id = None
            st.session_state.research_question_ids = []
            for k in (
                "user_request",
                "research_topic",
                "research_domain",
                "use_web_search",
                "routed_intent",
                "last_router_message",
                "bypass_questions_cache",
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


def render_research_app() -> None:
    settings = get_settings()
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
        "This AI DeepResearch Agent uses DeepSeek model to perform comprehensive research."
    )

    st.title("🔍 AI DeepResearch Agent with langchain")

    valid, err_msg = validate_required_keys(deepseek_api_key)
    if not valid:
        st.warning(f"⚠️ {err_msg}")
        return

    llm = build_llm(
        api_key=deepseek_api_key,
        model_id=settings.deepseek_model_id,
        base_url=settings.deepseek_base_url,
    )

    st.header("Research request")
    st.text_area(
        "用自然语言描述你的需求（解析后将填入下方主题/领域，可自行修改）",
        key="user_request",
        height=120,
        placeholder=(
            "例如：我想调研美国关税对半导体供应链的影响，领域为国际贸易与产业政策，需要联网资料。"
        ),
    )
    if st.button("解析意图", key="route_intent"):
        raw = (st.session_state.get("user_request") or "").strip()
        if not raw:
            st.warning("请先输入一段话。")
        else:
            ctx = {
                "research_topic": (st.session_state.get("research_topic") or "").strip(),
                "research_domain": (st.session_state.get("research_domain") or "").strip(),
                "has_questions": bool(st.session_state.get("questions")),
                "has_answers": bool(st.session_state.get("question_answers")),
            }
            with st.spinner("路由中..."):
                decision, _ = route_user_message(llm, raw, ctx, retry_policy, pipeline_metrics)

            st.session_state.routed_intent = decision.intent
            st.session_state.use_web_search = decision.need_web_search
            st.session_state.last_router_message = decision.reply_to_user or ""

            if decision.intent == "off_topic":
                st.info(decision.reply_to_user or "当前对话与深度调研无关。")
            elif decision.intent == "clarify":
                st.warning(decision.clarify_prompt or decision.reply_to_user or "信息不足，请补充。")
            else:
                if decision.topic:
                    st.session_state.research_topic = decision.topic
                if decision.domain:
                    st.session_state.research_domain = decision.domain
                if decision.intent == "regenerate_questions":
                    st.session_state.bypass_questions_cache = True
                if decision.reply_to_user:
                    st.success(decision.reply_to_user)
                st.success(
                    f"意图：**{decision.intent}** ｜ 联网搜索：**"
                    f"{'开' if decision.need_web_search else '关'}**"
                )

            if decision.intent == "report_only" and st.session_state.get("question_answers"):
                st.info("已识别为「仅生成报告」。若有调研结果，请点击 **Compile Final Report**。")

    c1, c2 = st.columns(2)
    with c1:
        st.text_input("Research topic（可编辑）", key="research_topic")
    with c2:
        st.text_input("Domain（可编辑）", key="research_domain")

    topic = (st.session_state.get("research_topic") or "").strip()
    domain = (st.session_state.get("research_domain") or "").strip()

    force_new_questions = bool(st.session_state.get("bypass_questions_cache")) or (
        st.session_state.get("routed_intent") == "regenerate_questions"
    )
    with st.spinner("🤖 Generating research questions..."):
        uid = int(st.session_state["user_id"])
        if force_new_questions:
            print("生成问题...")
            old_qids = list(st.session_state.get("research_question_ids") or [])
            research_redis_cache.delete_questions_bundle(uid, topic, domain)
            for qid in old_qids:
                research_redis_cache.delete_answer(int(qid))
            print(
                f"regenerate_questions: 已删除 Redis 问题包与 {len(old_qids)} 条答案缓存 "
                f"topic={topic!r}, domain={domain!r}"
            )
        rq = (
            None
            if force_new_questions
            else research_redis_cache.try_get_questions_bundle(uid, topic, domain)
        )
        if rq is not None:
            questions, q_ids, run_id = rq
            print(
                f"在 Redis 缓存中找到信息 key={research_redis_cache.questions_key(uid, topic, domain)!r}, "
                f"run_id={run_id}, topic={topic!r}, domain={domain!r}, "
                f"共 {len(questions)} 条问题，此操作无需调用api"
            )
            st.session_state.questions = questions
            st.session_state.research_question_ids = q_ids
            st.session_state.research_run_id = run_id
            st.session_state.question_answers = []
            st.session_state.report_content = ""
            st.session_state.research_complete = False
        else:
            db = _db()
            try:
                cached = (
                    None
                    if force_new_questions
                    else research_repository.try_get_cached_questions(db, uid, topic, domain)
                )
                if cached is not None:
                    questions, q_ids, run_id = cached
                    print(
                        f"在数据库 research_question 表中找到信息 "
                        f"run_id={run_id}, topic={topic!r}, domain={domain!r}, "
                        f"共 {len(questions)} 条问题，此操作无需调用api"
                    )
                    st.session_state.questions = questions
                    st.session_state.research_question_ids = q_ids
                    st.session_state.research_run_id = run_id
                    st.session_state.question_answers = []
                    st.session_state.report_content = ""
                    st.session_state.research_complete = False
                    research_redis_cache.set_questions_bundle(
                        uid,
                        topic,
                        domain,
                        run_id,
                        questions,
                        q_ids,
                        settings.redis_ttl_questions_seconds,
                    )
                else:
                    run = research_repository.create_research_run(
                        db, uid, topic, domain, settings.deepseek_model_id
                    )
                    db.commit()
                    db.refresh(run)

                    questions, q_outcome = generate_questions(
                        llm, topic, domain, retry_policy, pipeline_metrics
                    )
                    q_ids = research_repository.save_questions_for_run(db, int(run.id), questions)
                    db.commit()

                    st.session_state.questions = questions
                    st.session_state.research_question_ids = q_ids
                    st.session_state.research_run_id = int(run.id)
                    st.session_state.question_answers = []
                    st.session_state.report_content = ""
                    st.session_state.research_complete = False

                    research_redis_cache.set_questions_bundle(
                        uid,
                        topic,
                        domain,
                        int(run.id),
                        questions,
                        q_ids,
                        settings.redis_ttl_questions_seconds,
                    )

                    with st.expander("Diagnostics: question generation", expanded=False):
                        st.write(f"attempts={q_outcome.attempts}, retries={q_outcome.retry_count}")
                        for r in q_outcome.records:
                            st.write(r)
                if force_new_questions:
                    st.session_state.bypass_questions_cache = False
                    st.session_state.routed_intent = ""
            except Exception as e:
                st.error(f"generate_questions failed: {e}")
            finally:
                db.close()

    if st.session_state.questions:
        st.header("Research Questions")
        for i, question in enumerate(st.session_state.questions):
            st.markdown(f"**{i + 1}. {question}**")

    topic = (st.session_state.get("research_topic") or "").strip()
    domain = (st.session_state.get("research_domain") or "").strip()
    tavily_effective = (tavily_key or None) if st.session_state.get("use_web_search", True) else None

    if st.session_state.questions and st.button("Start Research", key="start_research"):
        st.header("Research Results")
        progress_bar = st.progress(0.0)

        run_id = st.session_state.get("research_run_id")
        qid_list = st.session_state.get("research_question_ids") or []

        if run_id is None or len(qid_list) != len(st.session_state.questions):
            st.error("缺少 research_run / question_id，请先重新生成问题。")
        elif not topic or not domain:
            st.error("缺少主题或领域，请先解析意图或填写 Research topic / Domain。")
        else:
            db = _db()
            question_answers: list[dict] = []
            try:
                for i, question in enumerate(st.session_state.questions):
                    progress_bar.progress(i / len(st.session_state.questions))
                    answer: str | None = None
                    qid = int(qid_list[i])
                    with st.spinner(f"🔍 Researching question {i + 1}..."):
                        answer = research_redis_cache.try_get_answer(qid)
                        if answer is not None:
                            print(
                                f"在 Redis 缓存中找到信息 key={research_redis_cache.answer_key(qid)!r}, "
                                f"question_id={qid}, run_id={run_id}，此操作无需调用api"
                            )
                        else:
                            cached_ans = research_repository.try_get_cached_answer(db, qid)
                            if cached_ans is not None:
                                print(
                                    f"在数据库 research_answer 表中找到信息 "
                                    f"question_id={qid}, run_id={run_id}，此操作无需调用api"
                                )
                                answer = cached_ans
                                research_redis_cache.set_answer(
                                    qid,
                                    answer,
                                    settings.redis_ttl_answer_seconds,
                                )
                            else:
                                try:
                                    answer, _ = research_one_question(
                                        llm,
                                        topic,
                                        domain,
                                        question,
                                        retry_policy,
                                        pipeline_metrics,
                                        tavily_api_key=tavily_effective,
                                    )
                                    research_repository.save_answer(db, int(run_id), qid, answer)
                                    db.commit()
                                    research_redis_cache.set_answer(
                                        qid,
                                        answer,
                                        settings.redis_ttl_answer_seconds,
                                    )
                                except Exception as e:
                                    st.error(f"research_one_question failed: {e}")

                    st.subheader(f"Question {i + 1}:")
                    st.markdown(f"**{question}**")
                    if answer is not None:
                        st.markdown(answer)
                        question_answers.append({"question": question, "answer": answer})
                    progress_bar.progress((i + 1) / len(st.session_state.questions))

                st.session_state.question_answers = question_answers
                st.session_state.research_complete = False
                st.session_state.report_content = ""
            except Exception as e:
                st.error(f"research failed: {e}")
            finally:
                db.close()

    topic = (st.session_state.get("research_topic") or "").strip()
    domain = (st.session_state.get("research_domain") or "").strip()

    if st.session_state.question_answers and st.button("Compile Final Report", key="compile_report"):
        if not topic or not domain:
            st.error("缺少主题或领域，请先解析意图或填写 Research topic / Domain。")
        else:
            with st.spinner("📝 Compiling final report..."):
                try:
                    report, _ = compile_report(
                        llm,
                        topic,
                        domain,
                        st.session_state.question_answers,
                        retry_policy,
                        pipeline_metrics,
                    )
                    st.session_state.report_content = report
                    st.session_state.research_complete = True
                except Exception as e:
                    st.error(f"compile_report failed: {e}")

    if st.session_state.research_complete and st.session_state.report_content:
        st.header("Final Report")
        st.success("Your report has been compiled.")
        with st.expander("View Full Report Content", expanded=True):
            st.markdown(st.session_state.report_content)


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