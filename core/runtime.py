from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from agents import RunConfig, Runner

from core.context import AppContext


@contextmanager
def optional_trace(name: str, group_id: str, metadata: dict[str, Any] | None = None) -> Iterator[None]:
    # Allow older SDK versions without RunConfig support.
    try:
        from agents.tracing import trace
    except ImportError:
        yield
        return
    with trace(name, group_id=group_id, metadata=metadata or {}):
        yield


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
