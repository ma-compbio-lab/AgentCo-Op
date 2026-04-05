from __future__ import annotations

from typing import Any, Dict, Mapping

from agentcoop.ir.schema import (
    IOContract,
    NodeKind,
    NodeSpec,
    SandboxSpec,
    ToolRef,
)
from agentcoop.workflows.design import TaskProfile

JsonDict = Dict[str, Any]


def _build_tool_node_from_cfg(node_id: str, role: str, description: str, payload: Mapping[str, Any]) -> NodeSpec:
    if not payload:
        raise ValueError(f"Missing tool-node configuration for {node_id}")
    sandbox_payload = payload.get("sandbox")
    io_payload = payload.get("io", {})
    return NodeSpec(
        node_id=node_id,
        kind=NodeKind.tool,
        role=str(payload.get("role", role)),
        description=str(payload.get("description", description)),
        tools=[ToolRef.model_validate(tool) for tool in payload.get("tools", [])],
        sandbox=SandboxSpec.model_validate(sandbox_payload) if sandbox_payload else None,
        io=IOContract.model_validate(io_payload or {}),
        meta=dict(payload.get("meta", {})),
    )


def _default_exact_solver_prompt(profile: TaskProfile) -> str:
    del profile
    return (
        "You are an exact-answer problem solver. Solve the task in task.description. "
        "Prefer exact symbolic or rational forms over decimal approximations unless the problem explicitly asks for a decimal. "
        "Check your candidate answer against the explicit constraints in the problem statement before returning it. "
        "If your derivation depends on an unverified assumption, or your answer may violate a stated constraint, set output.requires_review=true and explain why. "
        "Return JSON with output.final_answer, output.solution_outline, output.requires_review, output.uncertainty_reason, confidence, and summary."
    )


def _default_artifact_validator_prompt() -> str:
    return (
        "You are an artifact validator. Judge whether the produced artifact bundle matches the task intent, "
        "required artifacts, and scientific constraints. "
        "Return JSON with output.methodology_faithfulness, output.artifact_completeness, output.biological_plausibility, "
        "output.overall_verdict, output.should_refine, output.concerns, confidence, and summary."
    )


def _default_refiner_prompt(task_label: str) -> str:
    return (
        f"You are refining a {task_label} workflow after validation feedback. "
        "Update the plan conservatively, keep the task intent fixed, and return JSON with "
        "output.selected_repo, output.analysis_config, output.design_config, output.selected_specialists, "
        "output.handoff_contract, output.summary, and confidence. "
        "Include only the fields that make sense for the task."
    )
