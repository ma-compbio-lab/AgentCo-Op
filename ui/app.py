from __future__ import annotations

import streamlit as st
from omegaconf import OmegaConf

from chat.service import stream_chat_turn
from core.chat_store import ChatStore
from core.event_bus import EventBus
from run import to_app_config
from runtime import build_runtime
from utils import set_log_config


def build_runtime_from_config(app_cfg):
    from models import build_model_routing, configure_openai, ensure_api_key, validate_backend

    validate_backend(app_cfg.model.backend)
    configure_openai(app_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(app_cfg.model)
    return build_runtime(
        routing,
        log_dir=app_cfg.log_dir,
        repair=app_cfg.repair,
        memory_cfg=app_cfg.memory,
        tool_cfg=app_cfg.tool,
    )


def main() -> None:
    st.set_page_config(page_title="Agent-Cop Chat", layout="wide")
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600&display=swap');
        html, body, [class*="css"] {
            font-family: 'Space Grotesk', sans-serif;
        }
        .stApp {
            background: linear-gradient(135deg, #f7f2e8 0%, #eef7ff 55%, #fdf7f0 100%);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    cfg = OmegaConf.load("conf/config.yaml")
    app_cfg = to_app_config(cfg)
    set_log_config(
        enabled=False,
        level=app_cfg.log.level,
        use_color=False,
        use_icons=app_cfg.log.use_icons,
        preview_chars=app_cfg.log.preview_chars,
        show_prompts=app_cfg.log.show_prompts,
        show_outputs=app_cfg.log.show_outputs,
    )

    if "runtime" not in st.session_state:
        st.session_state.runtime = build_runtime_from_config(app_cfg)
    if "session_id" not in st.session_state:
        st.session_state.session_id = "default"
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "logs" not in st.session_state:
        st.session_state.logs = []

    chat_store = ChatStore(app_cfg.chat.db_path)

    with st.sidebar:
        st.header("Session")
        st.session_state.session_id = st.text_input("Session ID", st.session_state.session_id)
        col1, col2, col3 = st.columns(3)
        if col1.button("New"):
            st.session_state.session_id = f"session-{len(st.session_state.messages)}"
        if col2.button("Undo"):
            chat_store.undo_last_turn(st.session_state.session_id)
            if len(st.session_state.messages) >= 2:
                st.session_state.messages = st.session_state.messages[:-2]
        if col3.button("Clear"):
            chat_store.clear(st.session_state.session_id)
            st.session_state.messages = []
        st.header("Logs")
        log_box = st.empty()

    st.title("Agent-Cop")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_text = st.chat_input("Type your request")
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        with st.chat_message("user"):
            st.markdown(user_text)

        assistant_placeholder = st.chat_message("assistant").empty()
        buffer = ""
        event_bus = EventBus()

        for event in stream_chat_turn(
            user_text,
            app_cfg,
            session_id=st.session_state.session_id,
            event_bus=event_bus,
            runtime=st.session_state.runtime,
        ):
            if event.get("type") == "assistant_delta":
                delta = event["data"]["delta"]
                buffer += delta
                assistant_placeholder.markdown(buffer)
            if event.get("type") == "assistant_final":
                final_text = event["data"]["text"]
                if not buffer:
                    assistant_placeholder.markdown(final_text)
                st.session_state.messages.append({"role": "assistant", "content": final_text})
            if event.get("type") == "log":
                payload = event.get("data", {})
                module = event.get("module", "")
                step = event.get("step", "")
                message = event.get("message", "")
                st.session_state.logs.append(f"{module}/{step} | {message} | {payload}")
                log_box.code("\n".join(st.session_state.logs[-200:]))
            if event.get("type") == "section":
                title = event.get("title", "")
                module = event.get("module", "")
                st.session_state.logs.append(f"{module} | {title}")
                log_box.code("\n".join(st.session_state.logs[-200:]))


if __name__ == "__main__":
    main()
