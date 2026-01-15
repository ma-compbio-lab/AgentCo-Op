from __future__ import annotations

import inspect
from typing import Any

from agents import RunConfig, Runner
from types import SimpleNamespace

from core.context import AppContext


async def run_agent(
    agent,
    input_data: Any,
    ctx: AppContext,
    *,
    session=None,
    workflow_name: str,
    max_turns: int = 12,
    hooks=None,
):
    try:
        run_config = RunConfig(
            workflow_name=workflow_name,
            group_id=ctx.session_id,
            trace_metadata={"run_id": ctx.run_id},
        )
        return await Runner.run(
            starting_agent=agent,
            input=input_data,
            context=ctx,
            session=session,
            max_turns=max_turns,
            hooks=hooks,
            run_config=run_config,
        )
    except TypeError:
        return await Runner.run(
            starting_agent=agent,
            input=input_data,
            context=ctx,
            session=session,
            max_turns=max_turns,
            hooks=hooks,
        )


async def run_agent_streamed(
    agent,
    input_data: Any,
    ctx: AppContext,
    *,
    session=None,
    workflow_name: str,
    max_turns: int = 12,
    hooks=None,
    event_bus=None,
):
    if not hasattr(Runner, "run_streamed"):
        return await run_agent(
            agent,
            input_data,
            ctx,
            session=session,
            workflow_name=workflow_name,
            max_turns=max_turns,
            hooks=hooks,
        )
    try:
        run_config = RunConfig(
            workflow_name=workflow_name,
            group_id=ctx.session_id,
            trace_metadata={"run_id": ctx.run_id},
        )
        maybe_result = Runner.run_streamed(
            starting_agent=agent,
            input=input_data,
            context=ctx,
            session=session,
            max_turns=max_turns,
            hooks=hooks,
            run_config=run_config,
        )
    except TypeError:
        maybe_result = Runner.run_streamed(
            starting_agent=agent,
            input=input_data,
            context=ctx,
            session=session,
            max_turns=max_turns,
            hooks=hooks,
        )

    result = await maybe_result if inspect.isawaitable(maybe_result) else maybe_result

    if event_bus is not None:
        stream_iter = result.stream_events()
        if hasattr(stream_iter, "__aiter__"):
            async for event in stream_iter:
                if isinstance(event, dict):
                    event_type = event.get("type")
                    delta = event.get("delta")
                else:
                    event_type = getattr(event, "type", None)
                    delta = getattr(event, "delta", None)
                if event_type == "response.output_text.delta" and delta:
                    event_bus.emit("assistant_delta", {"delta": delta})
        else:
            for event in stream_iter:
                if isinstance(event, dict):
                    event_type = event.get("type")
                    delta = event.get("delta")
                else:
                    event_type = getattr(event, "type", None)
                    delta = getattr(event, "delta", None)
                if event_type == "response.output_text.delta" and delta:
                    event_bus.emit("assistant_delta", {"delta": delta})

    final_output = getattr(result, "final_output", None)
    if callable(final_output):
        final_output = final_output()
    if hasattr(final_output, "__await__"):
        final_output = await final_output
    if final_output is None and hasattr(result, "get_final_output"):
        final_output = await result.get_final_output()
    return SimpleNamespace(final_output=final_output, context_wrapper=getattr(result, "context_wrapper", None))
