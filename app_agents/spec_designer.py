from __future__ import annotations

from agents import Agent
from agents.agent_output import AgentOutputSchema

from core.contracts import TaskSpecPatch


def build_spec_designer_agent(model_name: str) -> Agent:
    return Agent(
        name="SpecDesigner",
        instructions=(
            "Design missing constraints and success criteria for the task. "
            "Output TaskSpecPatch JSON only. "
            "Keep constraints testable and minimal; avoid over-constraining. "
            "If evidence is provided, align criteria with it."
        ),
        model=model_name,
        output_type=AgentOutputSchema(TaskSpecPatch, strict_json_schema=False),
    )
