import asyncio
import json
import streamlit as st
from sqlalchemy.orm import Session

from app.cache.redis_client import get_redis_client
from app.config.settings import get_settings, validate_required_keys
from app.core import auth_service
from app.core import research_pipeline
from app.core.intent_router import route_user_message
from app.core.pipeline_policy import stages_for_intent
from app.core.resilience import PipelineMetrics, RetryPolicy
from app.db.session import SessionLocal
from app.integrations.llm_client import build_llm
from app.core.stm_router_context import prepare_stm_router_context
from app.db import research_repository

try:
    import httpx  # noqa: F401
    from app.a2a.a2a_json_client import send_json_message

    _A2A_AVAILABLE = True
except ImportError:
    _A2A_AVAILABLE = False


settings = get_settings()

# STM-1：会话内多轮改口（仅保留最近若干条，控制 token）
STM_MAX_TURNS = 10


def _db() -> Session:
    return SessionLocal()


_STM_HARD_MAX_TURNS = settings._STM_HARD_MAX_TURNS


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
    st.session_state.setdefault("pipeline_last_error", "")
    st.session_state.setdefault("use_a2a_orchestrator", False)
    st.session_state.setdefault(
        "a2a_base_url", f"http://127.0.0.1:{settings.a2a_orchestrator_port}"
    )
    st.session_state.setdefault("chat_turns", [])
    st.session_state.setdefault("stm_dialogue_summary", "")


async def _call_orchestrator(base_url: str, payload: dict) -> dict:
    return await send_json_message(base_url.rstrip("/"), payload)


def _run_orchestrator_via_a2a_sync(base_url: str, payload: dict) -> dict:
    return asyncio.run(_call_orchestrator(base_url, payload))


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
            st.session_state.questions = []
            st.session_state.question_answers = []
            st.session_state.report_content = ""
            st.session_state.research_complete = False
            st.session_state.research_run_id = None
            st.session_state.research_question_ids = []
            st.session_state.chat_turns = []
            for k in (
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
    # Router may pre-fill topic/domain; apply before widgets bind to these keys.
    if "_pending_research_topic" in st.session_state:
        st.session_state["research_topic"] = st.session_state.pop(
            "_pending_research_topic"
        )
    if "_pending_research_domain" in st.session_state:
        st.session_state["research_domain"] = st.session_state.pop(
            "_pending_research_domain"
        )

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
    st.sidebar.markdown("### A2A (Orchestrator)")
    if not _A2A_AVAILABLE:
        st.sidebar.warning('未安装依赖。请安装: pip install "a2a-sdk[http-server]" httpx')
        st.session_state.use_a2a_orchestrator = False
    else:
        st.session_state.use_a2a_orchestrator = True

        st.sidebar.text_input(
            "Orchestrator Base URL",
            key="a2a_base_url",
            value=f"http://127.0.0.1:{settings.a2a_orchestrator_port}",
            help="与 settings.a2a_orchestrator_port 一致",
        )

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

    # 历史恢复（登录用户）
    uid = int(st.session_state["user_id"])
    _render_history_restore_panel(uid)

    st.header("Research request")
    st.text_area(
        "用自然语言描述你的需求（路由后按意图一键执行：出题 / 调研 / 报告）",
        key="user_request",
        height=120,
        placeholder="例如：请对「美国关税对半导体供应链影响」做完整深度调研，领域为国际贸易与产业政策，需要联网。",
    )
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("Research topic（可编辑，路由会预填）", key="research_topic")
    with c2:
        st.text_input("Domain（可编辑）", key="research_domain")

    if st.button("一键执行", type="primary", key="one_shot_run"):
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
            # 压缩对话历史，准备意图路由上下文
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
            with st.spinner("意图路由..."):
                decision, _ = route_user_message(
                    llm, raw, ctx, retry_policy, pipeline_metrics
                )

            st.session_state.routed_intent = decision.intent
            st.session_state.use_web_search = decision.need_web_search
            st.session_state.last_router_message = decision.reply_to_user or ""

            if decision.intent in ("off_topic", "clarify"):
                st.session_state.pipeline_last_error = ""
                assist = (
                    decision.reply_to_user
                    or decision.clarify_prompt
                    or ("信息不足，请补充主题与领域。" if decision.intent == "clarify" else "")
                ).strip()
                if assist:
                    _append_stm_turn("assistant", assist)
                if decision.intent == "off_topic":
                    st.info(decision.reply_to_user or "当前对话与深度调研无关。")
                else:
                    st.warning(
                        decision.clarify_prompt
                        or decision.reply_to_user
                        or "信息不足，请补充主题与领域。"
                    )
            else:
                topic = (st.session_state.get("research_topic") or "").strip()
                domain = (st.session_state.get("research_domain") or "").strip()
                if decision.topic:
                    t = (decision.topic or "").strip()
                    if t:
                        topic = t
                    st.session_state["_pending_research_topic"] = topic
                if decision.domain:
                    d = (decision.domain or "").strip()
                    if d:
                        domain = d
                    st.session_state["_pending_research_domain"] = domain

                assist_lines: list[str] = []
                if (decision.reply_to_user or "").strip():
                    assist_lines.append(decision.reply_to_user.strip())
                assist_lines.append(
                    f"[路由] 主题: {topic or '(空)'}；领域: {domain or '(空)'}；意图: {decision.intent}"
                )
                _append_stm_turn("assistant", "\n".join(assist_lines))

                stages = stages_for_intent(decision.intent)
                force_new = decision.intent == "regenerate_questions" or bool(
                    st.session_state.get("bypass_questions_cache")
                )

                tavily_effective = (
                    (tavily_key or None)
                    if st.session_state.get("use_web_search", True)
                    else None
                )

                if decision.intent == "report_only" and not st.session_state.get(
                    "question_answers"
                ):
                    st.error("当前没有调研结果，无法只生成报告。请先执行含「调研」的意图。")
                elif not stages:
                    st.warning("该意图无可执行阶段。")
                elif not topic or not domain:
                    st.error("缺少主题或领域，请补充后再试。")
                else:
                    old_qids = [
                        int(x)
                        for x in (st.session_state.get("research_question_ids") or [])
                    ]
                    use_a2a = bool(
                        st.session_state.get("use_a2a_orchestrator")
                    ) and _A2A_AVAILABLE
                    a2a_url = (st.session_state.get("a2a_base_url") or "").strip()

                    err: str | None = None

                    if use_a2a:
                        if not a2a_url:
                            err = "已启用 A2A，但未填写 Orchestrator Base URL。"
                        else:
                            try:
                                with st.spinner("通过 A2A 调用 Orchestrator…"):
                                    payload = {
                                        "uid": uid,
                                        "topic": topic,
                                        "domain": domain,
                                        "stages": stages,
                                        "force_new": force_new,
                                        "old_question_ids": old_qids,
                                        "tavily_api_key": tavily_effective,
                                    }
                                    data = _run_orchestrator_via_a2a_sync(a2a_url, payload)
                                err = data.get("error")
                                if not err:
                                    st.session_state.questions = list(
                                        data.get("questions") or []
                                    )
                                    st.session_state.research_question_ids = [
                                        int(x) for x in (data.get("question_ids") or [])
                                    ]
                                    rid = data.get("run_id")
                                    st.session_state.research_run_id = (
                                        int(rid) if rid is not None else None
                                    )
                                    st.session_state.question_answers = list(
                                        data.get("question_answers") or []
                                    )
                                    st.session_state.report_content = str(
                                        data.get("report") or ""
                                    )
                                    st.session_state.research_complete = bool(
                                        data.get("research_complete")
                                    )
                            except Exception as e:
                                err = f"A2A 调用失败: {e}"
                    else:
                        db = _db()
                        questions: list[str] = []
                        q_ids: list[int] = []
                        run_id: int = 0
                        question_answers: list[dict] = list(
                            st.session_state.get("question_answers") or []
                        )

                        try:
                            for stage in stages:
                                if stage == "questions":
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
                                        err = str(qres[3])
                                        break
                                    questions, q_ids, run_id = qres[0], qres[1], qres[2]
                                    st.session_state.questions = questions
                                    st.session_state.research_question_ids = q_ids
                                    st.session_state.research_run_id = run_id
                                    st.session_state.question_answers = []
                                    st.session_state.report_content = ""
                                    st.session_state.research_complete = False

                                elif stage == "research":
                                    if not questions or not q_ids or not run_id:
                                        err = "缺少问题列表或 run，无法调研。"
                                        break
                                    question_answers, err = research_pipeline.run_research_phase(
                                        db,
                                        llm=llm,
                                        topic=topic,
                                        domain=domain,
                                        questions=questions,
                                        q_ids=q_ids,
                                        run_id=run_id,
                                        retry_policy=retry_policy,
                                        metrics=pipeline_metrics,
                                        tavily_api_key=tavily_effective,
                                        settings=settings,
                                    )
                                    if err:
                                        break
                                    st.session_state.question_answers = question_answers

                                elif stage == "report":
                                    qa = list(
                                        st.session_state.get("question_answers")
                                        or question_answers
                                    )
                                    if not qa:
                                        err = "没有 Q&A，无法生成报告。"
                                        break
                                    report, err = research_pipeline.run_report_phase(
                                        llm,
                                        topic,
                                        domain,
                                        qa,
                                        retry_policy,
                                        pipeline_metrics,
                                    )
                                    if err:
                                        break

                                    # 关键：写入 research_report（需要你在 repo 中实现 upsert_report）
                                    try:
                                        research_repository.upsert_report(
                                            db,
                                            int(run_id),
                                            str(report),
                                            format="markdown",
                                        )
                                        db.commit()
                                    except Exception as e:
                                        err = f"报告写入数据库失败: {e}"
                                        break

                                    st.session_state.report_content = report
                                    st.session_state.research_complete = True
                        finally:
                            db.close()

                    st.session_state.pipeline_last_error = err or ""
                    st.session_state.bypass_questions_cache = False
                    st.session_state.routed_intent = ""

                    if err:
                        st.error(f"流水线中断：{err}")
                    else:
                        mode = "A2A-Orchestrator" if use_a2a else "in-process"
                        st.success(f"完成（{mode}）。意图={decision.intent}，执行阶段={stages}")
                        if decision.reply_to_user:
                            st.caption(decision.reply_to_user)

    if st.session_state.get("pipeline_last_error"):
        with st.expander("上次流水线错误", expanded=False):
            st.code(st.session_state.pipeline_last_error)

    if st.session_state.get("routed_intent") or st.session_state.get("last_router_message"):
        with st.expander("上次路由摘要", expanded=False):
            st.write(f"intent: {st.session_state.get('routed_intent', '')}")
            st.write(st.session_state.get("last_router_message", ""))

    if st.session_state.questions:
        st.header("Research Questions")
        for i, question in enumerate(st.session_state.questions):
            st.markdown(f"**{i + 1}. {question}**")

    if st.session_state.get("question_answers"):
        st.header("Research Results")
        for i, qa in enumerate(st.session_state.question_answers):
            st.subheader(f"Question {i + 1}")
            st.markdown(f"**{qa['question']}**")
            st.markdown(qa.get("answer", ""))

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


if __name__ == "__main__":
    render_streamlit_app()