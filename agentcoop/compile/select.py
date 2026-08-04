"""Pareto selection over compiled candidates.

Selection is deliberately split from scoring. `estimate_utility` says what can
be known *before execution*, and it is scrupulous about what cannot: robustness
and scientific utility are marked unavailable at compile time, because nothing
has been run and pretending otherwise would let a workflow score well on
dimensions nobody measured.

`choose` then applies a declared :class:`SelectionPolicy` to the non-dominated
set and returns the rationale. There is no hidden weighting: if a caller wants
a scalar trade-off they must say so, and the fact that they did is recorded.
"""

from __future__ import annotations

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.compile.grammar import RuleContext
from agentcoop.ir.checks import CheckLevel, CheckReport, CheckStatus
from agentcoop.ir.evidence import EvidenceLedger
from agentcoop.ir.utility import (
    Objective,
    SelectionPolicy,
    UtilityVector,
    pareto_front,
    select,
)
from agentcoop.ir.workflow import CompiledWorkflow, atomics


class UtilityEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    vector: UtilityVector
    #: Why each measured coordinate has the value it does.
    basis: dict[str, str] = Field(default_factory=dict)


def estimate_utility(
    candidate_id: str,
    workflow: CompiledWorkflow,
    report: CheckReport,
    ledger: EvidenceLedger,
    ctx: RuleContext,
) -> UtilityEstimate:
    """Pre-execution utility. Honest about what has not been measured."""
    vector = UtilityVector()
    basis: dict[str, str] = {}

    # -- validity: static checks only -------------------------------------
    if report.failures(blocking_only=True):
        vector = vector.with_value(Objective.VALIDITY, 0.0)
        basis["validity"] = "a blocking static check failed"
    else:
        rate = report.pass_rate(CheckLevel.HARD)
        if rate is None:
            vector = vector.mark_unavailable(Objective.VALIDITY)
            basis["validity"] = "no hard check reached a verdict"
        else:
            vector = vector.with_value(Objective.VALIDITY, rate)
            basis["validity"] = f"{rate:.0%} of decided hard static checks passed"

    # -- evidence ----------------------------------------------------------
    vector = vector.with_value(Objective.EVIDENCE, ledger.evidence_coverage())
    basis["evidence"] = (
        f"{ledger.evidence_coverage():.0%} of design decisions are admissibly "
        f"justified; {ledger.observed_fraction():.0%} cite something executed"
    )

    # -- robustness / scientific utility: not knowable pre-execution -------
    vector = vector.mark_unavailable(Objective.ROBUSTNESS)
    basis["robustness"] = "unavailable before execution: nothing has been run"
    vector = vector.mark_unavailable(Objective.SCIENTIFIC_UTILITY)
    basis["scientific_utility"] = "unavailable before execution: no claim exists yet"

    # -- cost / latency ----------------------------------------------------
    total_usd, total_s = 0.0, 0.0
    priced = 0
    for atom in atomics(workflow.term):
        card = ctx.library.get(atom.component)
        if card is None:
            continue
        cost = card.expected_cost()
        total_usd += cost.usd
        total_s += cost.latency_s
        if cost.usd or cost.latency_s:
            priced += 1

    limits = ctx.dossier.limits
    if priced == 0:
        vector = vector.mark_unavailable(Objective.COST)
        vector = vector.mark_unavailable(Objective.LATENCY)
        basis["cost"] = "no component declares or has measured a cost"
    else:
        if limits.max_usd:
            vector = vector.with_value(Objective.COST, min(1.0, total_usd / limits.max_usd))
            basis["cost"] = f"${total_usd:.2f} of a ${limits.max_usd:.2f} budget"
        else:
            vector = vector.with_value(Objective.COST, total_usd)
            basis["cost"] = f"${total_usd:.2f} estimated (no budget declared)"
        if limits.max_wall_time_s:
            vector = vector.with_value(
                Objective.LATENCY, min(1.0, total_s / limits.max_wall_time_s)
            )
            basis["latency"] = f"{total_s:.0f}s of {limits.max_wall_time_s:.0f}s"
        else:
            vector = vector.with_value(Objective.LATENCY, total_s)
            basis["latency"] = f"{total_s:.0f}s estimated (no limit declared)"

    # -- risk: unverified components and silent failure exposure ----------
    nodes = atomics(workflow.term)
    if not nodes:
        vector = vector.mark_unavailable(Objective.RISK)
        basis["risk"] = "no component nodes"
    else:
        exposure = sum(
            1
            for r in report.results
            if r.check_id.startswith("silent_failure_exposure:")
            and r.status is CheckStatus.WARN
        )
        unverified = sum(
            1
            for r in report.results
            if r.check_id.startswith("missing_verifier:")
            and r.status is CheckStatus.FAIL
        )
        # Each extra component beyond what the task needs is added failure
        # surface: another environment, another handoff, another way to be
        # wrong. Counted here rather than against validity, because an
        # over-composed workflow is wasteful, not incorrect.
        over_composed = any(
            r.check_id == "unnecessary_composition" and r.status is CheckStatus.FAIL
            for r in report.results
        )
        surplus = (workflow.n_distinct_components - 1) if over_composed else 0
        risk = min(1.0, (exposure + unverified + surplus) / len(nodes))
        vector = vector.with_value(Objective.RISK, risk)
        basis["risk"] = (
            f"{exposure} silent-failure exposure(s), {unverified} unverified "
            f"requirement(s), {surplus} surplus component(s) across {len(nodes)} node(s)"
        )

    return UtilityEstimate(candidate_id=candidate_id, vector=vector, basis=basis)


class SelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chosen: Optional[str]
    front: list[str] = Field(default_factory=list)
    rationale: str = ""
    estimates: dict[str, UtilityEstimate] = Field(default_factory=dict)


def choose(
    estimates: Sequence[UtilityEstimate],
    policy: Optional[SelectionPolicy] = None,
) -> SelectionResult:
    """Pick from the non-dominated set under a declared policy."""
    if not estimates:
        return SelectionResult(chosen=None, rationale="no admissible candidates")

    policy = policy or SelectionPolicy(floors={Objective.VALIDITY: 1.0})
    pairs = [(e.candidate_id, e.vector) for e in estimates]
    chosen, front, rationale = select(pairs, policy)

    if chosen is None and policy.floors:
        # Every candidate violated a floor. Report the front without floors so
        # the caller can see what was on offer rather than an empty result.
        relaxed = pareto_front(pairs)
        return SelectionResult(
            chosen=None,
            front=relaxed,
            rationale=rationale,
            estimates={e.candidate_id: e for e in estimates},
        )

    return SelectionResult(
        chosen=chosen,
        front=front,
        rationale=rationale,
        estimates={e.candidate_id: e for e in estimates},
    )


__all__ = ["UtilityEstimate", "estimate_utility", "SelectionResult", "choose"]
