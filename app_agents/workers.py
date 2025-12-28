from __future__ import annotations

from agents import Agent, RunContextWrapper, function_tool

from core.context import AppContext


@function_tool
def cache_get(wrapper: RunContextWrapper[AppContext], key: str) -> str | None:
    """Get a cached value by key, or return null."""
    return wrapper.context.cache.get(key)


@function_tool
def cache_set(wrapper: RunContextWrapper[AppContext], key: str, value: str) -> str:
    """Set a cached value and return ok."""
    wrapper.context.cache.set(key, value)
    return "ok"


def build_worker_agent(model_name: str) -> Agent:
    return Agent(
        name="Worker",
        instructions=(
            "Solve assigned tasks accurately and concisely. "
            "Follow the task goal and constraints. "
            "Avoid unnecessary exploration or questions; make minimal assumptions and state them briefly. "
            "Produce the final answer directly."
        ),
        model=model_name,
        tools=[cache_get, cache_set],
    )
