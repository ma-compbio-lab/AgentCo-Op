from __future__ import annotations

from agents import Agent
from agents.agent_output import AgentOutputSchema

from core.contracts import ToolPlan


def build_tool_doc_synth_agent(model_name: str) -> Agent:
    return Agent(
        name="ToolDocSynth",
        instructions=(
            "Generate a runnable ToolPlan for the selected tool. "
            "Include container spec, run commands, and verification steps. "
            "Return ToolPlan JSON only."
        ),
        model=model_name,
        output_type=AgentOutputSchema(ToolPlan, strict_json_schema=False),
    )
