"""Shadow validation: does the patch actually help, and what did it break?

A patch that makes the failing check pass is not thereby a good patch. It may
have cost twice as much, dropped provenance, removed the verifier that was
catching the problem, or broken three checks that were passing. v1 had no way
to notice any of that, because it never re-evaluated after patching.

Validation therefore requires **both**:

1. the original failure is gone, and
2. no previously-passing check now fails.

Plus a utility delta so a patch that merely trades one dimension for another
is visible as such rather than counted as an improvement.

Components whose ``BehaviorContract.shadow_safe`` is False are never
speculatively executed. Re-running something with irreversible side effects to
see whether a guess was right is not a validation strategy.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.execute.engine import ExecutionResult
from agentcoop.ir.capability import ComponentLibrary, CostProfile
from agentcoop.ir.checks import CheckReport, CheckStatus
from agentcoop.ir.utility import Objective, UtilityVector, dominates
from agentcoop.ir.workflow import CompiledWorkflow, atomics
from agentcoop.repair.patches import Patch

#: Runs a workflow under reduced inputs and a shadow budget.
ShadowHarness = Callable[[CompiledWorkflow], Awaitable[ExecutionResult]]

#: Derives a utility vector from an execution result.
UtilityFn = Callable[[ExecutionResult], UtilityVector]


class ShadowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    fixed_original_failure: bool = False
    collateral_regressions: list[str] = Field(default_factory=list)
    delta_utility: dict[Objective, float] = Field(default_factory=dict)
    cost: CostProfile = Field(default_factory=CostProfile)
    #: Set when the patched workflow is Pareto-dominated by the original.
    dominated_by_original: bool = False
    notes: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return "patch validated"
        if self.collateral_regressions:
            return "rejected: collateral regression in " + ", ".join(
                self.collateral_regressions[:3]
            )
        if not self.fixed_original_failure:
            return "rejected: original failure persists"
        return "rejected: " + ("; ".join(self.notes) or "validation failed")


def passing_check_ids(report: CheckReport) -> set[str]:
    return {r.check_id for r in report.results if r.status is CheckStatus.PASS}


def failing_check_ids(report: CheckReport) -> set[str]:
    return {r.check_id for r in report.results if r.status is CheckStatus.FAIL}


class ShadowValidator:
    def __init__(
        self,
        library: ComponentLibrary,
        *,
        utility_fn: Optional[UtilityFn] = None,
        forbid_collateral_regression: bool = True,
    ) -> None:
        self.library = library
        self.utility_fn = utility_fn
        self.forbid_collateral_regression = forbid_collateral_regression

    def shadow_unsafe_components(self, workflow: CompiledWorkflow) -> list[str]:
        """Components that must not be speculatively re-run."""
        unsafe: list[str] = []
        for atom in atomics(workflow.term):
            card = self.library.get(atom.component)
            if card is not None and not card.behavior.shadow_safe:
                unsafe.append(atom.component)
        return sorted(set(unsafe))

    async def validate(
        self,
        patch: Patch,
        original: CompiledWorkflow,
        patched: CompiledWorkflow,
        baseline: ExecutionResult,
        harness: ShadowHarness,
    ) -> ShadowResult:
        unsafe = self.shadow_unsafe_components(patched)
        if unsafe:
            return ShadowResult(
                ok=False,
                notes=[
                    "not shadow-safe: "
                    + ", ".join(unsafe)
                    + " have irreversible side effects, so the patch cannot be "
                    "speculatively validated; escalate instead of guessing"
                ],
            )

        try:
            trial = await harness(patched)
        except Exception as exc:
            return ShadowResult(
                ok=False,
                notes=[f"shadow execution raised {type(exc).__name__}: {exc}"],
            )

        was_failing = failing_check_ids(baseline.report)
        was_passing = passing_check_ids(baseline.report)
        now_failing = failing_check_ids(trial.report)

        fixed = bool(was_failing) and not (was_failing & now_failing)
        regressions = sorted(was_passing & now_failing)

        delta: dict[Objective, float] = {}
        dominated = False
        if self.utility_fn is not None:
            before = self.utility_fn(baseline)
            after = self.utility_fn(trial)
            for dim in before.comparable_with(after):
                a, b = after.oriented(dim), before.oriented(dim)
                if a is not None and b is not None:
                    delta[dim] = a - b
            dominated = dominates(before, after)

        notes: list[str] = []
        if not was_failing:
            notes.append("baseline had no failing check; nothing to fix")
        if dominated:
            notes.append(
                "the patched workflow is Pareto-dominated by the original: it fixed "
                "the symptom while making the result worse overall"
            )

        ok = (
            fixed
            and not (self.forbid_collateral_regression and regressions)
            and not dominated
        )

        return ShadowResult(
            ok=ok,
            fixed_original_failure=fixed,
            collateral_regressions=regressions,
            delta_utility=delta,
            cost=trial.cost,
            dominated_by_original=dominated,
            notes=notes,
        )


__all__ = [
    "ShadowHarness",
    "UtilityFn",
    "ShadowResult",
    "ShadowValidator",
    "passing_check_ids",
    "failing_check_ids",
]
