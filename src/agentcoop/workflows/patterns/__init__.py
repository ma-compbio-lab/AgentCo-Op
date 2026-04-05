from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from agentcoop.ir.schema import BudgetSpec, ModelSpec, TaskSpec, WorkflowBlueprint
from agentcoop.workflows.design import BlueprintCompilerConfig, WorkflowPattern

JsonDict = Dict[str, Any]


@dataclass
class PatternBuildContext:
    task: TaskSpec
    budget: BudgetSpec
    meta: JsonDict
    model: ModelSpec
    review_model: ModelSpec
    design: BlueprintCompilerConfig
    resolved_config: Mapping[str, Any]


from agentcoop.workflows.patterns.direct_answer import build as _build_direct_answer
from agentcoop.workflows.patterns.reason_execute_select import build as _build_reason_execute_select
from agentcoop.workflows.patterns.code_generate_test_repair import build as _build_code_generate_test_repair
from agentcoop.workflows.patterns.repo_transfer_validate import build as _build_repo_transfer_validate
from agentcoop.workflows.patterns.closed_loop_design_validate import build as _build_closed_loop_design_validate
from agentcoop.workflows.patterns.specialist_assembly import build as _build_specialist_assembly
from agentcoop.workflows.patterns.synthesized_hybrid import build as _build_synthesized_hybrid

_PATTERN_BUILDERS = {
    WorkflowPattern.direct_answer.value: _build_direct_answer,
    WorkflowPattern.reason_execute_select.value: _build_reason_execute_select,
    WorkflowPattern.code_generate_test_repair.value: _build_code_generate_test_repair,
    WorkflowPattern.repo_transfer_validate.value: _build_repo_transfer_validate,
    WorkflowPattern.closed_loop_design_validate.value: _build_closed_loop_design_validate,
    WorkflowPattern.specialist_assembly.value: _build_specialist_assembly,
    WorkflowPattern.synthesized_hybrid.value: _build_synthesized_hybrid,
}


def build_pattern_blueprint(pattern_id: str, context: PatternBuildContext) -> WorkflowBlueprint:
    builder = _PATTERN_BUILDERS.get(pattern_id)
    if builder is None:
        raise ValueError(f"Unsupported workflow pattern: {pattern_id}")
    return builder(context)
