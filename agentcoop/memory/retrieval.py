"""Turning statistics into citable design evidence.

The compiler is not allowed to read a posterior and decide something. It has
to cite an :class:`~agentcoop.ir.evidence.EvidenceItem`, and every item this
module produces is built with
:func:`~agentcoop.ir.evidence.outcome_evidence`, which downgrades anything
resting on fewer than three observations to ``ASSERTED`` — and ``ASSERTED``
evidence is excluded from admissibility by
:meth:`DesignEvidenceRecord.kinds_present`. So "we ran it twice and it worked"
is *recorded*, is visible in the report, and still cannot justify adding a
fallback arm or binding a component.

Two rules are enforced here rather than left to the caller:

* ``n`` is the count of observations that reached a verdict. Runs nobody could
  evaluate are reported in the item's ``data`` but never counted toward the
  threshold, so an unavailable evaluator cannot promote evidence to
  ``STATISTICAL``.
* Claims are written from the numbers, including the negative ones. When there
  is no history the claim says so explicitly, which is what lets a
  ``DECLINE_STRUCTURE`` record state *why* extra structure was not warranted.
"""

from __future__ import annotations

from typing import Any, Optional

from agentcoop.ir.capability import ReliabilityPosterior
from agentcoop.ir.evidence import EvidenceItem, outcome_evidence
from agentcoop.ir.faults import FaultClass
from agentcoop.memory.statistics import ObservationCounts, StatisticsStore

#: Mirrors the threshold inside :func:`agentcoop.ir.evidence.outcome_evidence`.
#: Used only for human-readable claim text; ``test_memory`` asserts the two
#: stay in agreement so this constant cannot drift away from the IR.
MIN_STATISTICAL_OBSERVATIONS = 3

#: Credible-interval mass used for the reported bounds. Matches
#: ``ReliabilityPosterior.lcb``.
_CI_MASS = 0.9


def _round(value: float, places: int = 6) -> float:
    return round(float(value), places)


def _stats_payload(counts: ObservationCounts, posterior: ReliabilityPosterior) -> dict[str, Any]:
    lo, hi = posterior.credible_interval(_CI_MASS)
    return {
        "successes": counts.successes,
        "failures": counts.failures,
        "undetermined": counts.undetermined,
        "alpha": _round(posterior.alpha),
        "beta": _round(posterior.beta),
        "posterior_mean": _round(posterior.mean),
        "lcb": _round(lo),
        "ucb": _round(hi),
        "prior": "Beta(1,1)",
    }


def _support_phrase(counts: ObservationCounts) -> str:
    """Phrase describing what the number rests on, including what it does not."""
    if counts.n == 0:
        base = "no evaluated observations"
    else:
        base = f"{counts.successes}/{counts.n} evaluated observations succeeded"
    if counts.undetermined:
        base += (
            f"; {counts.undetermined} further run(s) could not be evaluated and are "
            "excluded (an unavailable evaluator is not a passing evaluator)"
        )
    return base


def _summary(posterior: ReliabilityPosterior, counts: ObservationCounts) -> str:
    if counts.n == 0:
        return "posterior remains the uninformative Beta(1,1) prior"
    return (
        f"posterior mean {posterior.mean:.3f}, 90% lower bound {posterior.lcb:.3f} "
        f"(Beta({posterior.alpha:g},{posterior.beta:g}))"
    )


# ---------------------------------------------------------------------------
# Component reliability
# ---------------------------------------------------------------------------


def component_reliability_evidence(
    statistics: StatisticsStore, component: str
) -> EvidenceItem:
    """Outcome evidence about whether a component does its job.

    Below three evaluated observations the item comes back ``ASSERTED``, so a
    ``BIND_COMPONENT`` decision cannot lean on it — binding must then be
    justified by probe (``CAPABILITY``) evidence instead, which is the correct
    ordering: certification first, history later.
    """
    counts = statistics.component_counts(component)
    posterior = counts.posterior
    claim = (
        f"component '{component}': {_support_phrase(counts)}; {_summary(posterior, counts)}"
    )
    return outcome_evidence(
        claim,
        f"stats:component:{component}",
        counts.n,
        component=component,
        **_stats_payload(counts, posterior),
    )


def failure_profile_evidence(
    statistics: StatisticsStore,
    component: str,
    *,
    top_k: int = 3,
    confirmed_only: bool = False,
) -> EvidenceItem:
    """Outcome evidence about *how* a component fails.

    ``n`` is the number of failures that carry a diagnosed root cause, not the
    number of failures: an undiagnosed failure tells us the component broke,
    not why, and a distribution built from symptoms would be exactly the
    symptom-to-action shortcut the rebuild removes.
    """
    distribution = statistics.failure_distribution(component, confirmed_only=confirmed_only)
    n_classified = statistics.failure_observations(component, confirmed_only=confirmed_only)
    ranked = sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0].value))[:top_k]
    top_text = ", ".join(f"{fc.value}={p:.3f}" for fc, p in ranked)
    if n_classified == 0:
        claim = (
            f"component '{component}' has no diagnosed failures on record; the "
            f"Laplace-smoothed fault distribution is uniform over the taxonomy ({top_text})"
        )
    else:
        claim = (
            f"component '{component}' has {n_classified} diagnosed failure(s); "
            f"Laplace-smoothed fault distribution leads with {top_text}"
        )
    return outcome_evidence(
        claim,
        f"stats:failure_distribution:{component}",
        n_classified,
        component=component,
        confirmed_only=confirmed_only,
        distribution={fc.value: _round(p) for fc, p in distribution.items()},
        observed_counts={
            fc.value: n
            for fc, n in statistics.observed_fault_counts(
                component, confirmed_only=confirmed_only
            ).items()
        },
    )


# ---------------------------------------------------------------------------
# Edge compatibility
# ---------------------------------------------------------------------------


def edge_compatibility_evidence(
    statistics: StatisticsStore, producer: str, consumer: str, artifact_type: str
) -> EvidenceItem:
    """Outcome evidence that two components have actually composed before.

    This is *not* a substitute for the static type/facet check. It answers a
    different question — "did this handoff ever really work?" — and a handoff
    whose facets were underspecified is recorded as undetermined upstream, so
    it can never accumulate here into apparent support.
    """
    counts = statistics.edge_counts(producer, consumer, artifact_type)
    posterior = counts.posterior
    claim = (
        f"handoff {producer} -> {consumer} carrying '{artifact_type}': "
        f"{_support_phrase(counts)}; {_summary(posterior, counts)}"
    )
    return outcome_evidence(
        claim,
        f"stats:edge:{producer}->{consumer}:{artifact_type}",
        counts.n,
        producer=producer,
        consumer=consumer,
        artifact_type=artifact_type,
        **_stats_payload(counts, posterior),
    )


# ---------------------------------------------------------------------------
# Repair success
# ---------------------------------------------------------------------------


def repair_success_evidence(
    statistics: StatisticsStore, fault_class: FaultClass, patch_family: str
) -> EvidenceItem:
    """Outcome evidence that a patch family fixes a particular root cause.

    Recall from :class:`~agentcoop.memory.store.RepairAttempt` that a patch
    which removes the original failure while regressing a previously passing
    check counts as a *failure* here. So a family that reliably makes the
    symptom disappear at the cost of evidence coverage accumulates negative
    evidence, exactly as it should.
    """
    counts = statistics.repair_counts(fault_class, patch_family)
    posterior = counts.posterior
    claim = (
        f"patch family '{patch_family}' against fault class '{fault_class.value}': "
        f"{_support_phrase(counts)}; {_summary(posterior, counts)}"
    )
    return outcome_evidence(
        claim,
        f"stats:repair:{fault_class.value}:{patch_family}",
        counts.n,
        fault_class=fault_class.value,
        patch_family=patch_family,
        **_stats_payload(counts, posterior),
    )


# ---------------------------------------------------------------------------
# Derived judgments
# ---------------------------------------------------------------------------


def fallback_warranted(
    statistics: StatisticsStore,
    component: str,
    *,
    min_failure_rate: float = 0.1,
) -> tuple[bool, EvidenceItem]:
    """Is a fallback arm for ``component`` justified by recorded outcomes?

    The grammar admits ``Fallback`` only when outcome evidence shows a
    non-trivial failure rate for the primary. "Non-trivial" is read
    conservatively: we require the *lower* bound of the failure probability to
    clear the threshold, which equals ``1 - ucb`` of the success posterior. One
    observed failure widens the interval instead of proving unreliability.

    Both gates must hold — the statistical threshold *and* load-bearing
    strength — so two failures out of two runs cannot buy a second component.
    Returning ``False`` with the item attached is what lets the compiler
    justify declining the extra structure rather than silently omitting it.
    """
    counts = statistics.component_counts(component)
    posterior = counts.posterior
    _, success_ucb = posterior.credible_interval(_CI_MASS)
    failure_lcb = 1.0 - success_ucb
    threshold_met = failure_lcb >= min_failure_rate

    if counts.n < MIN_STATISTICAL_OBSERVATIONS:
        verdict_text = (
            f"insufficient history to justify a fallback ({counts.n} evaluated "
            f"observation(s); {MIN_STATISTICAL_OBSERVATIONS} required for load-bearing "
            "outcome evidence)"
        )
    elif threshold_met:
        verdict_text = (
            f"failure rate is credibly at least {failure_lcb:.3f} "
            f"(>= {min_failure_rate:.3f}); a fallback arm is warranted"
        )
    else:
        verdict_text = (
            f"failure rate is not credibly above {min_failure_rate:.3f} "
            f"(lower bound {failure_lcb:.3f}); no fallback warranted"
        )

    claim = f"component '{component}': {_support_phrase(counts)}; {verdict_text}"
    item = outcome_evidence(
        claim,
        f"stats:component:{component}",
        counts.n,
        component=component,
        min_failure_rate=_round(min_failure_rate),
        failure_rate_lcb=_round(failure_lcb),
        threshold_met=threshold_met,
        **_stats_payload(counts, posterior),
    )
    return (threshold_met and item.load_bearing), item


def most_reliable_component(
    statistics: StatisticsStore, components: list[str]
) -> tuple[Optional[str], list[EvidenceItem]]:
    """Best candidate by lower confidence bound, with the evidence for each.

    Returns ``None`` when no candidate has load-bearing history: "we do not
    know which of these is better" is a real answer, and manufacturing a
    winner out of Beta(1,1) ties would be worse than admitting it.
    """
    ranked = statistics.rank_components(components)
    items = [component_reliability_evidence(statistics, name) for name, _ in ranked]
    load_bearing = [
        name for (name, _), item in zip(ranked, items, strict=True) if item.load_bearing
    ]
    return (load_bearing[0] if load_bearing else None), items


def retrieve_component_evidence(
    statistics: StatisticsStore, component: str
) -> list[EvidenceItem]:
    """Everything memory knows about one component, as citable items."""
    return [
        component_reliability_evidence(statistics, component),
        failure_profile_evidence(statistics, component),
    ]


def retrieve_edge_evidence(
    statistics: StatisticsStore, producer: str, consumer: str, artifact_types: list[str]
) -> list[EvidenceItem]:
    return [
        edge_compatibility_evidence(statistics, producer, consumer, artifact_type)
        for artifact_type in artifact_types
    ]


__all__ = [
    "MIN_STATISTICAL_OBSERVATIONS",
    "component_reliability_evidence",
    "failure_profile_evidence",
    "edge_compatibility_evidence",
    "repair_success_evidence",
    "fallback_warranted",
    "most_reliable_component",
    "retrieve_component_evidence",
    "retrieve_edge_evidence",
]
