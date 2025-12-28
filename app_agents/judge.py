from __future__ import annotations

from agents import Agent

from core.contracts import JudgeReport


def build_judge_agent(model_name: str) -> Agent:
    return Agent(
        name="Judge",
        instructions=(
            "Evaluate whether outputs satisfy success criteria. "
            "Return JudgeReport JSON with ok/score/issues/suggested_patch."
        ),
        model=model_name,
        output_type=JudgeReport,
    )


def build_aggregator_agent(model_name: str) -> Agent:
    return Agent(
        name="Aggregator",
        instructions="Fuse candidate outputs into a final response.",
        model=model_name,
    )
