from __future__ import annotations

from agents import Agent


def build_docker_repair_agent(model_name: str) -> Agent:
    return Agent(
        name="DockerRepair",
        instructions=(
            "Fix a Dockerfile based on build errors. "
            "Return the full corrected Dockerfile only; no extra text."
        ),
        model=model_name,
    )
