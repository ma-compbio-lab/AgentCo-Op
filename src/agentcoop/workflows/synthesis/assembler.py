from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

from agentcoop.ir.schema import WorkflowBlueprint
from agentcoop.workflows.synthesis.component_library import ComponentSpec

if TYPE_CHECKING:
    from agentcoop.workflows.patterns import PatternBuildContext


class SynthesisError(RuntimeError):
    """Raised when the LLM assembler cannot produce a valid blueprint."""


class SynthesisAssembler:
    """Calls the LLM with task + components, parses response into WorkflowBlueprint."""

    SYSTEM_PROMPT = (
        "You are a workflow architect for a multi-agent research automation system.\n"
        "Given a task description and a set of available workflow components, design a WorkflowBlueprint.\n\n"
        "Output ONLY valid JSON matching the WorkflowBlueprint Pydantic schema. No explanation, no markdown fences.\n\n"
        "Rules:\n"
        "- Use only the provided components as node templates (you may adapt their prompts).\n"
        "- base_nodes: list of NodeSpec objects (required).\n"
        "- base_edges: list of EdgeSpec objects connecting nodes (can be empty for single-node workflows).\n"
        "- subgraphs and gates: only include if needed.\n"
        "- task: copy the provided task spec exactly.\n"
        "- budget: copy the provided budget spec exactly.\n"
    )

    def __init__(self, llm_client: Any, model_spec: Any) -> None:
        self._client = llm_client
        self._model = model_spec

    def assemble(
        self,
        context: "PatternBuildContext",
        candidates: List[Tuple[ComponentSpec, float]],
    ) -> WorkflowBlueprint:
        component_summaries = [
            {
                "component_id": c.component_id,
                "role": c.role,
                "description": c.description,
                "capability_tags": c.capability_tags,
                "node_template": c.node_spec_template,
                "relevance_score": round(score, 4),
            }
            for c, score in candidates
        ]

        user_message = json.dumps({
            "task": context.task.model_dump(mode="json"),
            "budget": context.budget.model_dump(mode="json"),
            "available_components": component_summaries,
            "model": context.model.model_dump(mode="json"),
            "review_model": context.review_model.model_dump(mode="json"),
            "instructions": (
                "Design a WorkflowBlueprint for the given task. "
                "Select and adapt the most relevant components. "
                "Return only the JSON object."
            ),
        }, ensure_ascii=False, indent=2)

        response = self._client.complete(
            self._model,
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
        )

        raw_json = response.get("content", "")
        try:
            blueprint_dict = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise SynthesisError(
                f"LLM assembler returned non-JSON content: {raw_json[:200]!r}"
            ) from exc

        try:
            blueprint = WorkflowBlueprint.model_validate(blueprint_dict)
        except Exception as exc:
            raise SynthesisError(
                f"LLM-assembled blueprint failed schema validation: {exc}"
            ) from exc

        return blueprint
