from __future__ import annotations

from typing import TYPE_CHECKING

from agentcoop.ir.schema import WorkflowBlueprint

if TYPE_CHECKING:
    from agentcoop.workflows.patterns import PatternBuildContext


def build(context: PatternBuildContext) -> WorkflowBlueprint:
    profile = context.design.task_profile
    if profile.requires_specialists:
        from agentcoop.workflows.patterns.specialist_assembly import build as _build_specialist_assembly
        return _build_specialist_assembly(context)
    if profile.requires_closed_loop:
        from agentcoop.workflows.patterns.closed_loop_design_validate import build as _build_closed_loop
        return _build_closed_loop(context)
    if profile.requires_repo_search or profile.requires_web_research:
        from agentcoop.workflows.patterns.repo_transfer_validate import build as _build_repo_transfer
        return _build_repo_transfer(context)
    if profile.requires_test_execution or profile.answer_mode == "code":
        from agentcoop.workflows.patterns.code_generate_test_repair import build as _build_code_gen
        return _build_code_gen(context)
    if profile.requires_tool_execution or profile.answer_mode == "exact_answer":
        from agentcoop.workflows.patterns.reason_execute_select import build as _build_reason_exec
        return _build_reason_exec(context)
    from agentcoop.workflows.patterns.direct_answer import build as _build_direct
    return _build_direct(context)
