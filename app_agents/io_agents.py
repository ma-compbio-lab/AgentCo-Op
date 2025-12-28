from __future__ import annotations

from agents import Agent


def build_io_agent(model_name: str) -> Agent:
    return Agent(
        name="IOAgent",
        instructions="Normalize inputs/outputs to the requested format.",
        model=model_name,
    )
