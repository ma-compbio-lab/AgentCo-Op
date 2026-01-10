from __future__ import annotations

import asyncio
import threading
from typing import Iterable

from config import AppConfig
from core.chat_store import ChatStore
from core.contracts import TaskSpec
from core.event_bus import EventBus
from core.observability_hooks import EventStreamHooks
from methods.dispatcher import run_method
from runtime import build_runtime
from utils import log_event, set_event_sink


def build_chat_context(items: Iterable[dict]) -> str:
    lines: list[str] = []
    for item in items:
        role = item.get("role")
        content = item.get("content")
        if not role or content is None:
            continue
        prefix = "User" if role == "user" else "Assistant"
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


async def chat_turn_async(
    user_text: str,
    app_cfg: AppConfig,
    *,
    session_id: str,
    event_bus: EventBus | None = None,
    runtime=None,
) -> str:
    if runtime is None:
        from models import build_model_routing, configure_openai, ensure_api_key, validate_backend

        validate_backend(app_cfg.model.backend)
        configure_openai(app_cfg.model.api_key)
        ensure_api_key()
        routing = build_model_routing(app_cfg.model)
        runtime = build_runtime(
            routing,
            log_dir=app_cfg.log_dir,
            repair=app_cfg.repair,
            memory_cfg=app_cfg.memory,
            tool_cfg=app_cfg.tool,
            mcp_cfg=app_cfg.mcp,
        )

    chat_store = ChatStore(app_cfg.chat.db_path)
    recent = await chat_store.get_recent_async(session_id, limit_turns=app_cfg.chat.history_turns)
    chat_context = build_chat_context(recent)

    task_cfg = app_cfg.task
    task = TaskSpec(
        goal=user_text,
        constraints=task_cfg.constraints,
        success_criteria=task_cfg.success_criteria,
        budget_tokens=task_cfg.budget_tokens,
        input_modalities=task_cfg.input_modalities,
        output_modalities=task_cfg.output_modalities,
        allow_web_search_for_spec=task_cfg.allow_web_search_for_spec,
        spec_max_search_queries=task_cfg.spec_max_search_queries,
        allow_tool_search=task_cfg.allow_tool_search,
        tool_max_candidates=task_cfg.tool_max_candidates,
        tool_max_search_queries=task_cfg.tool_max_search_queries,
        chat_context=chat_context,
    )

    prev_hooks = runtime.run_hooks
    prev_event_bus = getattr(runtime.context, "event_bus", None)
    prev_stream = getattr(runtime.context, "stream_output", False)
    if event_bus:
        runtime.context.event_bus = event_bus
        runtime.context.stream_output = bool(app_cfg.chat.stream)
        runtime.run_hooks = EventStreamHooks(event_bus)
        set_event_sink(event_bus.publish)

    try:
        log_event("RUN", "chat", "chat turn start", data={"session_id": session_id})
        result = await run_method(app_cfg.method, task, runtime)
        answer = result.answer
        if event_bus:
            event_bus.emit("assistant_final", {"text": answer})
        await chat_store.append_turn_async(session_id, user_text, answer)
        return answer
    except Exception as exc:  # noqa: BLE001 - surface errors to the chat loop
        if event_bus:
            event_bus.emit("error", {"message": str(exc)}, level="error")
        raise
    finally:
        if event_bus:
            set_event_sink(None)
            runtime.context.event_bus = prev_event_bus
            runtime.context.stream_output = prev_stream
            runtime.run_hooks = prev_hooks


def stream_chat_turn(
    user_text: str,
    app_cfg: AppConfig,
    *,
    session_id: str,
    event_bus: EventBus,
    runtime=None,
):
    queue = event_bus.subscribe_queue()

    def runner() -> None:
        asyncio.run(
            chat_turn_async(
                user_text,
                app_cfg,
                session_id=session_id,
                event_bus=event_bus,
                runtime=runtime,
            )
        )

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    while True:
        event = queue.get()
        yield event
        if event.get("type") == "assistant_final":
            break
