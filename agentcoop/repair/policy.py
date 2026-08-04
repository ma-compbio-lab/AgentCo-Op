"""Tier escalation.

Three distinct processes that v1 ran together as "repair":

* **contract repair** restores executability and hard invariants;
* **local optimization** improves utility once the contract holds;
* **global redesign** changes how the task is decomposed.

Escalation is triggered by *recurrence*, not by frustration. If the same fault
class keeps reappearing after committed repairs, the repairs were addressing a
symptom and the structure is at fault. That is a fact about the log, so the
decision is computable rather than judgemental.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from agentcoop.ir.faults import FaultClass, RepairTier
from agentcoop.ir.workflow import RepairPolicy
from agentcoop.repair.transaction import RepairLog


class TierDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Optional[RepairTier]
    allowed: bool
    reason: str
    escalated_from: Optional[RepairTier] = None


ORDER = [
    RepairTier.CONTRACT_REPAIR,
    RepairTier.LOCAL_OPTIMIZATION,
    RepairTier.GLOBAL_REDESIGN,
]

BUDGET_FIELD = {
    RepairTier.CONTRACT_REPAIR: "max_contract_repairs",
    RepairTier.LOCAL_OPTIMIZATION: "max_local_optimizations",
    RepairTier.GLOBAL_REDESIGN: "max_global_redesigns",
}


def budget_for(policy: RepairPolicy, tier: RepairTier) -> int:
    return int(getattr(policy, BUDGET_FIELD[tier]))


def decide_tier(
    fault: FaultClass,
    natural_tier: RepairTier,
    log: RepairLog,
    policy: RepairPolicy,
) -> TierDecision:
    """Which tier may act on this fault right now."""
    recurrences = log.fault_recurrences(fault)
    tier = natural_tier
    escalated_from: Optional[RepairTier] = None

    if recurrences >= policy.escalate_after_repeats:
        index = ORDER.index(natural_tier)
        if index + 1 < len(ORDER):
            escalated_from = natural_tier
            tier = ORDER[index + 1]

    used = log.committed_in_tier(tier)
    budget = budget_for(policy, tier)
    if used >= budget:
        return TierDecision(
            tier=tier,
            allowed=False,
            reason=(
                f"{tier.value} budget exhausted ({used}/{budget}); further repair "
                "at this tier would be unbounded local patching"
            ),
            escalated_from=escalated_from,
        )

    if escalated_from is not None:
        return TierDecision(
            tier=tier,
            allowed=True,
            reason=(
                f"'{fault.value}' has recurred {recurrences} times, so repairs at "
                f"{escalated_from.value} were addressing a symptom; escalating to "
                f"{tier.value}"
            ),
            escalated_from=escalated_from,
        )

    return TierDecision(
        tier=tier,
        allowed=True,
        reason=f"{tier.value} is the natural tier for '{fault.value}' ({used}/{budget} used)",
    )


def total_budget_exhausted(log: RepairLog, policy: RepairPolicy) -> bool:
    return all(
        log.committed_in_tier(tier) >= budget_for(policy, tier) for tier in ORDER
    )


__all__ = ["TierDecision", "decide_tier", "budget_for", "total_budget_exhausted", "ORDER"]
