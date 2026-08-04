"""Run state and budget accounting.

Budget enforcement lives here rather than inside the engine loop so that
shadow validation can run a patched workflow under an explicitly *reduced*
budget without touching execution logic. Speculative repair testing must not
be able to consume the whole run's money.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.capability import CostProfile
from agentcoop.ir.dossier import ResourceLimits


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    BUDGET_USD = "budget_usd_exhausted"
    BUDGET_TOKENS = "budget_tokens_exhausted"
    BUDGET_WALL_TIME = "wall_time_exhausted"
    BUDGET_CALLS = "component_call_limit"
    BLOCKING_FAILURE = "blocking_check_failed"
    ADAPTER_MISSING = "adapter_not_registered"
    HUMAN_GATE = "awaiting_human_review"
    MERGE_REFUSED = "merge_refused"


class BudgetLedger(BaseModel):
    """Tracks spend against declared limits.

    Every ``exceeded`` decision names *which* limit was hit, because a run that
    stopped for wall-clock and a run that stopped for money are different
    findings and get different repairs.
    """

    model_config = ConfigDict(extra="forbid")

    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    spent: CostProfile = Field(default_factory=CostProfile)
    component_calls: int = 0

    def charge(self, cost: CostProfile) -> None:
        self.spent = self.spent + cost
        self.component_calls += 1

    def scaled_for_shadow(self) -> "BudgetLedger":
        """A fresh ledger holding only the reserved shadow fraction.

        Uses the *remaining* budget, not the original, so a run that has
        already spent most of its money cannot fund an expensive speculative
        validation.
        """
        fraction = max(0.0, min(1.0, self.limits.shadow_budget_fraction))
        remaining_usd = None
        if self.limits.max_usd is not None:
            remaining_usd = max(0.0, self.limits.max_usd - self.spent.usd) * fraction
        remaining_tokens = None
        if self.limits.max_tokens is not None:
            remaining_tokens = int(
                max(0, self.limits.max_tokens - self.spent.tokens) * fraction
            )
        remaining_time = None
        if self.limits.max_wall_time_s is not None:
            remaining_time = max(
                0.0, self.limits.max_wall_time_s - self.spent.latency_s
            ) * fraction
        return BudgetLedger(
            limits=ResourceLimits(
                max_usd=remaining_usd,
                max_tokens=remaining_tokens,
                max_wall_time_s=remaining_time,
                max_component_calls=self.limits.max_component_calls,
                max_repair_transactions=self.limits.max_repair_transactions,
                shadow_budget_fraction=0.0,
            )
        )

    def exceeded(self) -> Optional[TerminationReason]:
        lim = self.limits
        if lim.max_usd is not None and self.spent.usd >= lim.max_usd:
            return TerminationReason.BUDGET_USD
        if lim.max_tokens is not None and self.spent.tokens >= lim.max_tokens:
            return TerminationReason.BUDGET_TOKENS
        if lim.max_wall_time_s is not None and self.spent.latency_s >= lim.max_wall_time_s:
            return TerminationReason.BUDGET_WALL_TIME
        if (
            lim.max_component_calls is not None
            and self.component_calls >= lim.max_component_calls
        ):
            return TerminationReason.BUDGET_CALLS
        return None

    def headroom_fraction(self) -> Optional[float]:
        """Fraction of the tightest declared budget still unspent."""
        fractions: list[float] = []
        lim = self.limits
        if lim.max_usd:
            fractions.append(1.0 - self.spent.usd / lim.max_usd)
        if lim.max_tokens:
            fractions.append(1.0 - self.spent.tokens / lim.max_tokens)
        if lim.max_wall_time_s:
            fractions.append(1.0 - self.spent.latency_s / lim.max_wall_time_s)
        return min(fractions) if fractions else None


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: str
    task_id: str
    budget: BudgetLedger = Field(default_factory=BudgetLedger)
    executed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    terminated: bool = False
    termination_reason: Optional[TerminationReason] = None
    notes: list[str] = Field(default_factory=list)

    def terminate(self, reason: TerminationReason, note: str = "") -> None:
        self.terminated = True
        self.termination_reason = reason
        if note:
            self.notes.append(note)

    @property
    def ok(self) -> bool:
        return not self.terminated and not self.failed


__all__ = ["TerminationReason", "BudgetLedger", "RunState"]
