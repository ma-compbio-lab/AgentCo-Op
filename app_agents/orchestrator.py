from __future__ import annotations

from agents import Agent

from core.contracts import ExecutionPlan


def build_planner_agent(model_name: str) -> Agent:
    return Agent(
        name="Orchestrator",
        instructions=(
            "You are an orchestration planner. "
            "Given the task and available agents, output a valid ExecutionPlan JSON. "
            "Use only the provided agent IDs in active_agents/subtasks."
        ),
        model=model_name,
        output_type=ExecutionPlan,
    )
