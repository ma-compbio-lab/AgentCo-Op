from __future__ import annotations

from agents import Agent
from agents.agent_output import AgentOutputSchema

from core.contracts import JudgeReport


def build_judge_agent(model_name: str) -> Agent:
    return Agent(
        name="Judge",
        instructions=(
            "Evaluate whether outputs satisfy success criteria. "
            "Return JudgeReport JSON with ok/score/issues/suggested_patch. "
            "Be strict: list missing criteria and conflicts. "
            "Return JSON only; no extra text."
        ),
        model=model_name,
        output_type=AgentOutputSchema(JudgeReport, strict_json_schema=False),
    )


def build_aggregator_agent(model_name: str) -> Agent:
    return Agent(
        name="Aggregator",
        instructions=(
            "Fuse candidate outputs into a final response. "
            "Resolve conflicts and align with constraints; if uncertainty remains, add a short Notes section."
        ),
        model=model_name,
    )
