"""The compiler: dossier in, justified workflow out.

Order of operations, and each step can veto:

1. **Dossier defects.** A required output with no producing subgoal is a
   specification fault. Compiling anyway would produce a workflow that cannot
   possibly succeed, and the failure would surface much later looking like a
   component problem.
2. **Enumerate** candidates the grammar licenses — always including the
   single-component candidate when one exists.
3. **Justify.** A candidate containing a decision without load-bearing
   evidence is rejected, not annotated.
4. **Statically analyse.** Blocking failures eliminate the candidate.
5. **Estimate utility**, honestly marking what cannot be known pre-execution.
6. **Pareto-select** under a declared policy.

The result records every rejection with its reason, so `agentcoop explain` can
answer "why not that other workflow?" as well as "why this one?".
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.compile.candidates import (
    CandidateWorkflow,
    EnumerationResult,
    enumerate_candidates,
)
from agentcoop.compile.grammar import RuleContext
from agentcoop.compile.justify import JustificationResult, justify
from agentcoop.compile.select import (
    SelectionResult,
    UtilityEstimate,
    choose,
    estimate_utility,
)
from agentcoop.compile.static_analysis import analyze
from agentcoop.ir.artifacts import TypeRegistry
from agentcoop.ir.capability import ComponentLibrary
from agentcoop.ir.checks import CheckReport
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.utility import SelectionPolicy
from agentcoop.ir.workflow import CompiledWorkflow, RepairPolicy
from agentcoop.merges import MergeRegistry, default_merge_registry


class CompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: Optional[CompiledWorkflow] = None
    candidates: list[CandidateWorkflow] = Field(default_factory=list)
    static_reports: dict[str, CheckReport] = Field(default_factory=dict)
    justifications: dict[str, JustificationResult] = Field(default_factory=dict)
    #: Candidate ID -> executable workflow that passed justification and static analysis.
    workflows: dict[str, CompiledWorkflow] = Field(default_factory=dict)
    estimates: dict[str, UtilityEstimate] = Field(default_factory=dict)
    selection: Optional[SelectionResult] = None
    dossier_defects: list[str] = Field(default_factory=list)
    #: candidate id -> why it was eliminated.
    rejected: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.workflow is not None

    @property
    def pareto_front(self) -> list[str]:
        return self.selection.front if self.selection else []

    def explain(self) -> str:
        """Human-readable account of the whole decision. Used by the CLI."""
        lines: list[str] = []
        if self.dossier_defects:
            lines.append("SPECIFICATION DEFECTS")
            lines.extend(f"  - {d}" for d in self.dossier_defects)
            lines.append("")

        lines.append(f"CANDIDATES ({len(self.candidates)})")
        for candidate in self.candidates:
            mark = "*" if self.selection and candidate.candidate_id == self.selection.chosen else " "
            status = self.rejected.get(candidate.candidate_id, "admissible")
            lines.append(
                f" {mark} {candidate.candidate_id}"
                f" [{', '.join(candidate.components)}] -- {status}"
            )
        lines.append("")

        if self.selection:
            lines.append(f"PARETO FRONT: {', '.join(self.selection.front) or 'empty'}")
            lines.append(f"SELECTED    : {self.selection.chosen or 'none'}")
            lines.append(f"RATIONALE   : {self.selection.rationale}")
            lines.append("")

        if self.workflow is not None:
            lines.append("DESIGN EVIDENCE")
            for row in self.workflow.evidence.summary_rows():
                flag = "ok " if row["admissible"] else "BAD"
                lines.append(
                    f"  [{flag}] {row['decision_id']}: {row['kind']} -> {row['target']}"
                    f"  (evidence: {', '.join(row['evidence_kinds']) or 'none'};"
                    f" {row['n_alternatives']} alternative(s) considered)"
                )
        return "\n".join(lines)


class Compiler:
    """Compiles a dossier into a justified, statically verified workflow."""

    def __init__(
        self,
        *,
        types: Optional[TypeRegistry] = None,
        merges: Optional[MergeRegistry] = None,
        statistics: Any = None,
        policy: Optional[SelectionPolicy] = None,
        repair_policy: Optional[RepairPolicy] = None,
        max_candidates: int = 16,
    ) -> None:
        self.types = types or TypeRegistry()
        self.merges = merges or default_merge_registry()
        self.statistics = statistics
        self.policy = policy
        self.repair_policy = repair_policy or RepairPolicy()
        self.max_candidates = max_candidates

    def context(
        self, dossier: TaskEvidenceDossier, library: ComponentLibrary
    ) -> RuleContext:
        return RuleContext(
            dossier=dossier,
            library=library,
            types=self.types,
            merges=self.merges,
            statistics=self.statistics,
        )

    def compile(
        self,
        dossier: TaskEvidenceDossier,
        library: ComponentLibrary,
        *,
        workflow_id: str = "wf",
        allow_defects: bool = False,
    ) -> CompilationResult:
        result = CompilationResult(dossier_defects=list(dossier.specification_defects()))
        if result.dossier_defects and not allow_defects:
            result.notes.append(
                "refusing to compile a dossier with specification defects; fix the "
                "task description first (pass allow_defects=True to override)"
            )
            return result

        ctx = self.context(dossier, library)
        enumeration: EnumerationResult = enumerate_candidates(
            dossier, ctx, max_candidates=self.max_candidates
        )
        result.candidates = enumeration.candidates
        result.notes.extend(enumeration.notes)
        for subgoal_id, refusals in sorted(enumeration.unbindable.items()):
            result.rejected[f"subgoal::{subgoal_id}"] = (
                "no bindable component: " + "; ".join(refusals[:3])
            )

        estimates: list[UtilityEstimate] = []
        for candidate in enumeration.candidates:
            workflow = CompiledWorkflow(
                workflow_id=f"{workflow_id}::{candidate.candidate_id}",
                task_id=dossier.task_id,
                term=candidate.term,
                evidence=candidate.ledger,
                repair_policy=self.repair_policy,
                compile_notes=list(candidate.construction_log),
                provenance=[f"candidate:{candidate.candidate_id}"],
            )

            justification = justify(candidate, ctx)
            result.justifications[candidate.candidate_id] = justification
            if not justification.admissible:
                result.rejected[candidate.candidate_id] = (
                    "unjustified: " + "; ".join(justification.rejections[:2])
                )
                continue

            report = analyze(workflow, ctx, ledger=candidate.ledger)
            result.static_reports[candidate.candidate_id] = report
            blocking = report.failures(blocking_only=True)
            if blocking:
                result.rejected[candidate.candidate_id] = (
                    "static analysis: " + "; ".join(c.summary for c in blocking[:2])
                )
                continue

            result.workflows[candidate.candidate_id] = workflow

            estimate = estimate_utility(
                candidate.candidate_id, workflow, report, candidate.ledger, ctx
            )
            result.estimates[candidate.candidate_id] = estimate
            estimates.append(estimate)

        selection = choose(estimates, self.policy)
        result.selection = selection

        if selection.chosen is None:
            result.notes.append(
                "no candidate was selected; see rejected candidates and the Pareto front"
            )
            return result

        selected_workflow = result.workflows[selection.chosen].model_copy(
            update={
                "provenance": [
                    *result.workflows[selection.chosen].provenance,
                    f"selection:{selection.rationale}",
                ]
            }
        )
        result.workflows[selection.chosen] = selected_workflow
        result.workflow = selected_workflow
        return result


__all__ = ["Compiler", "CompilationResult"]
