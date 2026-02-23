from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
        planning_cfg=app_cfg.planning,
        tool_cfg=app_cfg.tool,
        exec_cfg=app_cfg.exec,
        mcp_cfg=app_cfg.mcp,
        adaptive_cfg=app_cfg.adaptive,
    )


def main() -> None:
    st.set_page_config(page_title="Agent-Cop Chat", layout="wide")
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Sora:wght@300;400;600&family=Space+Grotesk:wght@500;700&display=swap');
        :root {
            --bg-0: #0a0f16;
            --bg-1: #0f1622;
            --panel: rgba(18, 24, 36, 0.92);
            --panel-2: rgba(14, 19, 30, 0.9);
            --border: #223047;
            --text: #e6edf7;
            --muted: #9aa9c0;
            --accent: #4fd1c5;
            --accent-2: #f6c453;
            --shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
        }
        html, body, [class*="css"] {
            font-family: 'Sora', sans-serif;
            color: var(--text);
        }
        .stApp {
            background:
                radial-gradient(900px 600px at 8% -10%, rgba(79, 209, 197, 0.18), transparent 60%),
                radial-gradient(800px 520px at 92% 0%, rgba(246, 196, 83, 0.14), transparent 55%),
                linear-gradient(160deg, var(--bg-0) 0%, var(--bg-1) 40%, #0b1527 100%);
        }
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2rem;
        }
        section[data-testid="stSidebar"] > div {
            background: var(--panel-2);
            border-right: 1px solid var(--border);
        }
        .stSidebar h1, .stSidebar h2, .stSidebar h3 {
            font-family: 'Space Grotesk', sans-serif;
            letter-spacing: 0.02em;
        }
        .stButton > button {
            background: linear-gradient(120deg, rgba(79, 209, 197, 0.22), rgba(79, 209, 197, 0.08));
            border: 1px solid rgba(79, 209, 197, 0.45);
            color: var(--text);
            box-shadow: var(--shadow);
            transition: transform 0.12s ease, border 0.12s ease;
        }
        .stButton > button:hover {
            transform: translateY(-1px);
            border-color: rgba(79, 209, 197, 0.75);
        }
        .stTextInput input {
            background: rgba(12, 16, 26, 0.9);
            border: 1px solid var(--border);
            color: var(--text);
        }
        div[data-testid="stChatMessage"] {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 0.75rem 1rem;
            box-shadow: var(--shadow);
        }
        div[data-testid="stChatMessage"][aria-label="user"] {
            background: linear-gradient(120deg, rgba(79, 209, 197, 0.18), rgba(79, 209, 197, 0.06));
            border-color: rgba(79, 209, 197, 0.45);
        }
        div[data-testid="stChatMessage"][aria-label="assistant"] {
            background: linear-gradient(140deg, rgba(19, 26, 38, 0.95), rgba(15, 20, 32, 0.92));
        }
        div[data-testid="stChatInput"] textarea {
            background: rgba(12, 16, 26, 0.95);
            border: 1px solid var(--border);
            color: var(--text);
        }
        div[data-testid="stChatInput"] textarea:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 2px rgba(79, 209, 197, 0.2);
        }
        div[data-testid="stCode"] {
            background: rgba(8, 12, 20, 0.95);
            border: 1px solid var(--border);
            font-family: 'JetBrains Mono', monospace;
            color: #cfe3ff;
        }
        .hero {
            background: linear-gradient(120deg, rgba(79, 209, 197, 0.15), rgba(246, 196, 83, 0.1));
            border: 1px solid rgba(79, 209, 197, 0.25);
            border-radius: 18px;
            padding: 1.1rem 1.4rem;
            margin-bottom: 1.2rem;
            box-shadow: var(--shadow);
            animation: rise 0.5s ease;
        }
        .hero-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.8rem;
            letter-spacing: 0.04em;
        }
        .hero-subtitle {
            color: var(--muted);
            margin-top: 0.35rem;
            font-size: 0.95rem;
        }
        @keyframes rise {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
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

    st.markdown(
        """
        <div class="hero">
            <div class="hero-title">Agent-Cop</div>
            <div class="hero-subtitle">Multi-agent workflow console with live tracing</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
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
