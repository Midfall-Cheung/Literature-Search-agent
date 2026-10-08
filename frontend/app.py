from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

# Streamlit prepends frontend/ to sys.path; its app.py must not shadow the
# backend app package when launched as `streamlit run frontend/app.py`.
if __package__ in (None, ""):
    project_root = str(Path(__file__).resolve().parents[1])
    if sys.path[0] != project_root:
        if project_root in sys.path:
            sys.path.remove(project_root)
        sys.path.insert(0, project_root)

import streamlit as st

from app.schemas.question import LANGUAGE_CODES
from frontend.api_client import APIClient, APIError
from frontend.terms import render_terms, execute_action, workspace

FIELD_LABELS = {
    "research_objective": "研究目标", "population_or_object": "研究对象",
    "intervention_or_exposure": "核心干预 / 暴露 / 概念", "comparator": "对照",
    "outcomes": "关注结果", "context": "研究场景", "study_types": "研究类型",
    "date_from": "起始年份", "date_to": "结束年份", "languages": "文献语言",
    "must_include": "必须纳入", "exclude": "排除条件", "databases": "数据库",
    "fulltext_requirement": "全文要求", "framework": "研究框架",
}
REVISION_FIELDS = (
    "population_or_object", "intervention_or_exposure", "outcomes",
    "study_types", "date_from", "date_to", "languages",
)
LIST_FIELDS = {"outcomes", "study_types", "languages"}
LANGUAGE_LABELS = {"zh": "中文", "en": "英文", "ja": "日语", "fr": "法语", "de": "德语"}


def queue_action(action: str, **payload: Any) -> None:
    """Queue once in the callback, then render disabled controls before HTTP."""
    if st.session_state.get("busy"):
        return
    drafts = {
        key: value for key, value in st.session_state.items()
        if key in {"original_question", "load_project_id", "revision_field", "workspace_page"}
        or key.startswith(("answer:", "revision:", "term:"))
    }
    st.session_state.action_drafts = drafts
    st.session_state.restore_drafts = drafts
    st.session_state.busy = True
    st.session_state.pending_action = (action, payload)
    st.session_state.error = None
    st.session_state.notice = None


def queue_create() -> None:
    queue_action("create", question=st.session_state.original_question)


def queue_load() -> None:
    queue_action("load", project_id=st.session_state.load_project_id)


def queue_answer(key: str, project_id: str) -> None:
    queue_action("answer", project_id=project_id, content=st.session_state[key])


def queue_revision(field: str, mode_key: str, value_key: str, feedback_key: str, project_id: str) -> None:
    mode = st.session_state[mode_key]
    if mode == "不限":
        updates = {"explicitly_unrestricted": [field]}
    elif mode == "未指定（空值）":
        updates = {field: None}
    else:
        value = st.session_state[value_key]
        if field in {"date_from", "date_to"}:
            if value is None:
                st.session_state.error = "请填写年份，或选择“未指定（空值）”或“不限”。"
                return
            value = int(value)
        elif field in LIST_FIELDS and field != "languages":
            value = [item.strip() for item in re.split(r"[\n,，、]+", value) if item.strip()]
        updates = {field: value}
    queue_action(
        "revise", project_id=project_id, field_updates=updates,
        feedback=st.session_state[feedback_key] or None,
    )


def run_pending(client: APIClient) -> None:
    pending = st.session_state.pop("pending_action", None)
    if pending is None:
        return
    action, payload = pending
    try:
        with st.spinner("正在与后端同步…"):
            if action.startswith("terms_"):
                execute_action(client, action, payload)
            elif action == "health":
                client.health()
                st.session_state.notice = "后端连接正常"
            else:
                if action == "create":
                    state = client.create_project(payload["question"])
                elif action in {"load", "refresh"}:
                    state = client.get_state(payload["project_id"])
                elif action == "answer":
                    state = client.answer(payload["project_id"], payload["content"])
                elif action == "confirm":
                    state = client.confirm(payload["project_id"])
                elif action == "revise":
                    state = client.revise(
                        payload["project_id"], payload["field_updates"], payload["feedback"]
                    )
                else:
                    raise APIError("未知页面操作，请刷新页面。")
                # Commit only a successful response, including revisions that reopen clarification.
                st.session_state.project_state = state
                st.session_state.project_id = state["project_id"]
                if action in {"create", "load", "refresh", "confirm"}:
                    workspace(state["project_id"])["needs_refresh"] = True
                if action == "create":
                    st.session_state.clear_original = True
                st.session_state.notice = "操作成功，已显示后端最新状态"
                if action == "revise":
                    # Keep the successful response even if the follow-up GET fails.
                    st.session_state.project_state = client.get_state(state["project_id"])
    except APIError as exc:
        st.session_state.error = str(exc)
    finally:
        st.session_state.busy = False
        # Toggling disabled changes widget identity in Streamlit. Restore drafts
        # before registering enabled widgets, including after a failed request.
        st.session_state.restore_drafts = st.session_state.pop("action_drafts", {})
    st.rerun()


def render_revision(state: dict, busy: bool) -> None:
    st.subheader("修订研究问题")
    field = st.selectbox(
        "选择修订字段", REVISION_FIELDS, format_func=FIELD_LABELS.get,
        key="revision_field", disabled=busy,
    )
    spec = state["question_spec"]
    # The server version owns defaults. Invalid submits keep the same widget keys and draft.
    prefix = f"revision:{state['project_id']}:{state.get('updated_at', '')}:{field}"
    mode_key, value_key, feedback_key = prefix + ":mode", prefix + ":value", prefix + ":feedback"
    modes = ["设置具体值", "不限"] if field in LIST_FIELDS else ["设置具体值", "未指定（空值）", "不限"]
    current = spec.get(field)
    default_mode = "不限" if field in spec.get("explicitly_unrestricted", []) else (
        "未指定（空值）" if current is None and field not in LIST_FIELDS else "设置具体值"
    )
    st.session_state.setdefault(mode_key, default_mode)
    mode = st.radio("字段边界", modes, index=None, key=mode_key, disabled=busy)
    if field == "languages":
        default_value = current or []
    elif field in {"date_from", "date_to"}:
        default_value = current if current is not None else 2020
    elif field in LIST_FIELDS:
        default_value = "\n".join(current or [])
    else:
        default_value = current or ""
    st.session_state.setdefault(value_key, default_value)
    with st.form("revision_form"):
        disabled = busy or mode != "设置具体值"
        if field == "languages":
            st.multiselect(
                "文献语言代码", sorted(LANGUAGE_CODES),
                format_func=lambda code: f"{code} · {LANGUAGE_LABELS.get(code, code)}",
                key=value_key, disabled=disabled,
            )
        elif field in {"date_from", "date_to"}:
            st.number_input(
                FIELD_LABELS[field], min_value=1500, max_value=2100,
                value=None, step=1,
                key=value_key, disabled=disabled,
            )
            st.caption("空值表示尚未指定；不限表示明确不限制该年份边界。")
        elif field in LIST_FIELDS:
            st.text_area("修订值（每行一项，也可用逗号分隔）", key=value_key, disabled=disabled)
        else:
            st.text_input("修订值", key=value_key, disabled=disabled)
        st.text_input("修订说明（可选）", key=feedback_key, disabled=busy)
        st.form_submit_button(
            "提交修订", disabled=busy, on_click=queue_revision,
            args=(field, mode_key, value_key, feedback_key, state["project_id"]),
        )


def render_project(state: dict, busy: bool) -> None:
    project_id = state["project_id"]
    st.subheader("当前项目")
    st.code(project_id, language=None)
    st.button("刷新项目状态", key="refresh", disabled=busy, on_click=queue_action, args=("refresh",), kwargs={"project_id": project_id})
    status = state["status"]
    if status != "confirmed":
        with st.expander("对话记录", expanded=True):
            for message in state.get("messages", []):
                with st.chat_message(message["role"]):
                    st.text(message["content"])
    if status == "clarifying":
        question = state.get("current_question")
        if not question:
            st.warning("后端尚未返回当前问题，请刷新项目状态。")
            return
        st.subheader("当前澄清问题")
        st.text(question["prompt"])
        st.caption(
            f"第 {question['round_number']}/{state.get('max_rounds', 6)} 轮 · "
            + "、".join(FIELD_LABELS.get(field, field) for field in question["target_fields"])
        )
        answer_key = f"answer:{project_id}:{state.get('clarification_round', 0)}"
        with st.form("answer_form"):
            st.text_area("你的回答", key=answer_key, disabled=busy)
            st.caption("日期和语言可直接用自然语言回答；不限制请回答“不限”。")
            st.form_submit_button("提交回答", disabled=busy, on_click=queue_answer, args=(answer_key, project_id))
    elif status == "awaiting_confirmation":
        st.subheader("待确认摘要")
        st.text(state.get("summary") or "暂无摘要，请刷新项目状态。")
        st.info("请检查摘要及系统假设，必须明确确认才能完成阶段 A。")
        st.button("确认研究问题", key="confirm", disabled=busy, on_click=queue_action, args=("confirm",), kwargs={"project_id": project_id})
        render_revision(state, busy)
    elif status == "confirmed":
        st.success("阶段 A 已确认")
        st.text(state.get("summary") or "")
        st.info("阶段 A 已完成；阶段 B 检索词表（P1-02）可从工作区入口访问。")
    else:
        st.warning("无法识别后端状态，请刷新项目状态。")
    with st.expander("结构化研究问题", expanded=status == "confirmed"):
        spec = state["question_spec"]
        unrestricted = spec.get("explicitly_unrestricted", [])
        st.table([
            {"字段": label, "当前值": "不限" if field in unrestricted else str(spec.get(field) if spec.get(field) not in (None, []) else "未指定")}
            for field, label in FIELD_LABELS.items()
        ])
        st.caption("缺失字段：" + ("、".join(FIELD_LABELS.get(field, field) for field in state.get("missing_fields", [])) or "无"))
    st.caption("系统假设")
    for assumption in state.get("assumptions", []):
        st.text(assumption)
    if not state.get("assumptions"):
        st.caption("无")


def main() -> None:
    st.set_page_config(page_title="文献检索 Agent · 阶段 A / B", page_icon="📚", layout="centered")
    st.title("文献检索 Agent · 阶段 A / B")
    st.session_state.setdefault("busy", False)
    for key, value in st.session_state.pop("restore_drafts", {}).items():
        st.session_state[key] = value
    for key in st.session_state.pop("reset_term_consent", []):
        st.session_state[key] = False
    if st.session_state.pop("clear_original", False):
        st.session_state.original_question = ""
    client = st.session_state.get("_api_client") or APIClient()
    busy = st.session_state.busy
    if st.session_state.get("error"):
        st.error(st.session_state.error)
    if st.session_state.get("notice"):
        st.success(st.session_state.notice)
    st.button("检查后端连接", key="health", disabled=busy, on_click=queue_action, args=("health",))
    with st.form("create_form"):
        st.text_area("原始研究问题", key="original_question", max_chars=5000, disabled=busy)
        st.form_submit_button("创建项目", disabled=busy, on_click=queue_create)
    with st.form("load_form"):
        st.text_input("已有项目 ID", key="load_project_id", disabled=busy)
        st.form_submit_button("加载项目", disabled=busy, on_click=queue_load)
    st.caption("请保存项目 ID；浏览器刷新后可重新输入 ID 加载。请求超时后先刷新项目，核对是否已保存。")
    if st.session_state.get("project_state"):
        state = st.session_state.project_state
        st.caption(f"当前项目：{state['project_id']} · 阶段 A 状态：{state['status']}")
        page = st.selectbox("工作区", ["阶段 A", "阶段 B"], key="workspace_page", disabled=busy)
        if page == "阶段 A":
            render_project(state, busy)
        else:
            render_terms(state, client, busy, queue_action)
    run_pending(client)


if __name__ == "__main__":
    main()
