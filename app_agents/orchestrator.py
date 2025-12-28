from __future__ import annotations

from agents import Agent

from core.contracts import ExecutionPlan


def build_planner_agent(model_name: str) -> Agent:
    return Agent(
        name="Orchestrator",
        instructions=(
            "You are an orchestration planner. "
            "Given the task and available agents, output a valid ExecutionPlan JSON. "
            "Use only the provided agent IDs in active_agents and subtasks. "
            "Pick one protocol from: pipeline, roundtable, debate, loop, hybrid. "
            "Prefer minimal, executable plans (avoid unnecessary steps). "
            "If information is missing, make a reasonable default and record it in meta. "
            "Return JSON only; do not add commentary."
        ),
        model=model_name,
        output_type=ExecutionPlan,
    )
