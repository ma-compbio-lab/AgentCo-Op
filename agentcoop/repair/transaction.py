"""Repair as a transaction.

Each attempted repair records the state it started from, the patch, the shadow
verdict, and whether it committed or rolled back. Because patch application is
pure, "rollback" is just declining to adopt the new workflow — there is no
half-patched state to recover from.

The log is the audit trail the paper reports over: repair precision, collateral
regression rate, and time-to-recovery are all computed from it rather than
asserted.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.faults import FaultClass, RepairTier
from agentcoop.ir.workflow import CompiledWorkflow
from agentcoop.repair.patches import Patch
from agentcoop.repair.shadow import ShadowResult


class TransactionOutcome(str, Enum):
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    #: The patch could not be applied at all (bad target, missing parameter).
    NOT_APPLICABLE = "not_applicable"
    #: Terminal patch: the loop stops rather than producing a new workflow.
    TERMINAL = "terminal"
    #: Shadow validation was refused (not shadow-safe).
    ESCALATED = "escalated"


class RepairTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    #: Structural key of the workflow before the patch — the rollback point.
    pre_state_key: str
    post_state_key: Optional[str] = None
    patch: Patch
    shadow: Optional[ShadowResult] = None
    outcome: TransactionOutcome = TransactionOutcome.ROLLED_BACK
    reason: str = ""
    #: Step in the repair loop, for time-to-recovery.
    attempt: int = 0

    @property
    def committed(self) -> bool:
        return self.outcome is TransactionOutcome.COMMITTED

    @property
    def rolled_back(self) -> bool:
        return self.outcome is TransactionOutcome.ROLLED_BACK

    @property
    def fault_class(self) -> FaultClass:
        return self.patch.hypothesis.fault_class

    @property
    def tier(self) -> RepairTier:
        return self.patch.tier


class RepairLog(BaseModel):
    """Ordered record of every repair attempt in a run."""

    model_config = ConfigDict(extra="forbid")

    transactions: list[RepairTransaction] = Field(default_factory=list)

    def append(self, transaction: RepairTransaction) -> RepairTransaction:
        self.transactions.append(transaction)
        return transaction

    def committed(self) -> list[RepairTransaction]:
        return [t for t in self.transactions if t.committed]

    def by_tier(self, tier: RepairTier) -> list[RepairTransaction]:
        return [t for t in self.transactions if t.tier is tier]

    def committed_in_tier(self, tier: RepairTier) -> int:
        return len([t for t in self.by_tier(tier) if t.committed])

    def fault_recurrences(self, fault: FaultClass) -> int:
        """How many times this fault class has been diagnosed.

        The escalation trigger: the same cause coming back after a committed
        repair means the repair addressed a symptom, and the tier should rise.
        """
        return len([t for t in self.transactions if t.fault_class is fault])

    def attempted_patch_ids(self) -> set[str]:
        return {t.patch.patch_id for t in self.transactions}

    def collateral_regression_rate(self) -> Optional[float]:
        """Fraction of *validated* patches that broke a passing check."""
        validated = [t for t in self.transactions if t.shadow is not None]
        if not validated:
            return None
        broke = sum(1 for t in validated if t.shadow.collateral_regressions)
        return broke / len(validated)

    def time_to_recovery(self) -> Optional[int]:
        """Attempts taken before the first successful commit."""
        for transaction in self.transactions:
            if transaction.committed:
                return transaction.attempt
        return None


__all__ = ["TransactionOutcome", "RepairTransaction", "RepairLog"]
