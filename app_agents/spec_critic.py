from __future__ import annotations

from agents import Agent
from agents.agent_output import AgentOutputSchema

from core.contracts import TaskSpecPatch


def build_spec_critic_agent(model_name: str) -> Agent:
    return Agent(
        name="SpecCritic",
        instructions=(
            "Review proposed constraints and success criteria for completeness and testability. "
            "Fix contradictions or missing edge cases. "
            "Output TaskSpecPatch JSON only."
        ),
        model=model_name,
        output_type=AgentOutputSchema(TaskSpecPatch, strict_json_schema=False),
    )
