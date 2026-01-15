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


@function_tool
def local_execute(
    wrapper: RunContextWrapper[AppContext],
    command: str,
    cwd: str | None = None,
) -> dict[str, object]:
    """Run a local shell command with workspace-only write restrictions."""
    executor = getattr(wrapper.context, "local_executor", None)
    if executor is None:
        return {"ok": False, "error": "Local execution is not configured."}
    try:
        result = executor.execute(command, cwd=cwd)
    except Exception as exc:  # noqa: BLE001 - return tool errors to the agent
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **result}


def build_worker_agent(model_name: str) -> Agent:
    return Agent(
        name="Worker",
        instructions=(
            "Solve assigned tasks accurately and concisely. "
            "Follow the task goal and constraints. "
            "Avoid unnecessary exploration or questions; make minimal assumptions and state them briefly. "
            "Produce the final answer directly. "
            "Use local_execute only when filesystem interaction is required; writes are restricted to the workspace."
        ),
        model=model_name,
        tools=[cache_get, cache_set, local_execute],
    )
