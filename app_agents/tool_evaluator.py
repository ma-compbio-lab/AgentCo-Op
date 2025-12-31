from __future__ import annotations

from agents import Agent
from agents.agent_output import AgentOutputSchema

from core.contracts import ToolCandidate


def build_tool_evaluator_agent(model_name: str) -> Agent:
    return Agent(
        name="ToolEvaluator",
        instructions=(
            "Select the best tool candidate for the task based on constraints and reliability. "
            "Return ToolCandidate JSON only."
        ),
        model=model_name,
        output_type=AgentOutputSchema(ToolCandidate, strict_json_schema=False),
    )
