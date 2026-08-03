"""Multi-objective utility without a scalar reward.

Open-ended scientific tasks do not have a reward function. v1 handled that by
hand-weighting coverage, complexity, cost, and risk into one number, which
buries every trade-off in constants nobody can defend.

Here a workflow's quality is a *vector*, and "better" means Pareto dominance.
Selection from the non-dominated set is a separate, explicit act — by declared
lexicographic priority, by expert pairwise preference, or by a scalarization
the user chooses and which is recorded as such.

Dimensions (all normalized to "higher is better" internally):

``validity``            hard invariants and schema checks that hold
``evidence``            design decisions that are admissibly justified
``robustness``          stability under reseeding, thresholds, and perturbation
``scientific_utility``  claim support, sensitivity coverage, provenance depth
``cost``                money and tokens spent            (minimized)
``latency``             wall-clock                        (minimized)
``risk``                unverified steps, silent-failure exposure (minimized)
"""

from __future__ import annotations

import itertools
import math
from enum import Enum
from typing import Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field


class Objective(str, Enum):
    VALIDITY = "validity"
    EVIDENCE = "evidence"
    ROBUSTNESS = "robustness"
    SCIENTIFIC_UTILITY = "scientific_utility"
    COST = "cost"
    LATENCY = "latency"
    RISK = "risk"


#: True when larger raw values are better.
MAXIMIZE: dict[Objective, bool] = {
    Objective.VALIDITY: True,
    Objective.EVIDENCE: True,
    Objective.ROBUSTNESS: True,
    Objective.SCIENTIFIC_UTILITY: True,
    Objective.COST: False,
    Objective.LATENCY: False,
    Objective.RISK: False,
}


class UtilityVector(BaseModel):
    """A workflow's quality along non-commensurable axes.

    ``unavailable`` records which dimensions could not be measured. A missing
    dimension is not zero — treating an unmeasured claim score as 0.0 would
    make an unevaluable workflow look strictly worse than a bad one, and
    treating it as 1.0 would be worse still.
    """

    model_config = ConfigDict(extra="forbid")

    values: dict[Objective, float] = Field(default_factory=dict)
    unavailable: set[Objective] = Field(default_factory=set)

    # -- construction -------------------------------------------------------

    @classmethod
    def of(cls, **kwargs: float) -> "UtilityVector":
        values = {Objective(k): float(v) for k, v in kwargs.items()}
        missing = {o for o in Objective if o not in values}
        return cls(values=values, unavailable=missing)

    def with_value(self, objective: Objective, value: float) -> "UtilityVector":
        values = {**self.values, objective: float(value)}
        unavailable = set(self.unavailable) - {objective}
        return UtilityVector(values=values, unavailable=unavailable)

    def mark_unavailable(self, objective: Objective) -> "UtilityVector":
        values = {k: v for k, v in self.values.items() if k != objective}
        return UtilityVector(values=values, unavailable=set(self.unavailable) | {objective})

    # -- access -------------------------------------------------------------

    def get(self, objective: Objective) -> Optional[float]:
        return self.values.get(objective)

    def oriented(self, objective: Objective) -> Optional[float]:
        """Value re-oriented so that larger is always better."""
        raw = self.values.get(objective)
        if raw is None:
            return None
        return raw if MAXIMIZE[objective] else -raw

    @property
    def measured(self) -> set[Objective]:
        return set(self.values)

    def comparable_with(self, other: "UtilityVector") -> set[Objective]:
        """Dimensions both vectors actually measured.

        Dominance is only ever decided over these. Comparing on a dimension
        one side never measured would silently invent a winner.
        """
        return self.measured & other.measured


def dominates(a: UtilityVector, b: UtilityVector, *, strict_dims: Optional[set[Objective]] = None) -> bool:
    """True iff ``a`` Pareto-dominates ``b``.

    Restricted to the dimensions both measured. If they share no measured
    dimension, neither dominates — an honest incomparability rather than an
    arbitrary tie-break.
    """
    dims = a.comparable_with(b)
    if strict_dims is not None:
        dims = dims & strict_dims
    if not dims:
        return False
    at_least_as_good = True
    strictly_better = False
    for dim in dims:
        av, bv = a.oriented(dim), b.oriented(dim)
        if av is None or bv is None:
            continue
        if av < bv - 1e-12:
            at_least_as_good = False
            break
        if av > bv + 1e-12:
            strictly_better = True
    return at_least_as_good and strictly_better


def pareto_front(
    candidates: Sequence[tuple[str, UtilityVector]],
    *,
    dims: Optional[set[Objective]] = None,
) -> list[str]:
    """Ids of the non-dominated candidates, in input order."""
    front: list[str] = []
    for name, vec in candidates:
        if any(
            dominates(other_vec, vec, strict_dims=dims)
            for other_name, other_vec in candidates
            if other_name != name
        ):
            continue
        front.append(name)
    return front


#: Normalized coordinate assigned to the worst observed value on a dimension.
#: Strictly positive so that the nadir point still encloses a sliver of
#: volume — otherwise a two-candidate comparison where each side is worst on
#: some axis collapses to zero hypervolume for both and tells us nothing.
_NADIR_FLOOR = 0.05


def _normalize(
    vectors: Sequence[UtilityVector],
    dims: Sequence[Objective],
    *,
    context: Optional[Sequence[UtilityVector]] = None,
) -> tuple[list[list[float]], dict[Objective, tuple[float, float]]]:
    """Min-max normalize oriented values onto ``[_NADIR_FLOOR, 1]`` per dimension.

    ``context`` supplies the population the bounds are computed over. This
    matters: to compare a workflow against its baselines the whole set must
    share one normalization, otherwise every vector normalizes to 1.0 in
    isolation and every comparison is a tie.

    Unmeasured dimensions map to 0.0 — below the nadir floor — so a workflow
    gets no hypervolume credit for an objective it never evaluated.
    """
    population = list(context) if context else list(vectors)
    bounds: dict[Objective, tuple[float, float]] = {}
    for dim in dims:
        present = [x for x in (v.oriented(dim) for v in population) if x is not None]
        lo = min(present) if present else 0.0
        hi = max(present) if present else 1.0
        bounds[dim] = (lo, hi)

    span = 1.0 - _NADIR_FLOOR
    points: list[list[float]] = []
    for vec in vectors:
        row: list[float] = []
        for dim in dims:
            lo, hi = bounds[dim]
            raw = vec.oriented(dim)
            if raw is None:
                row.append(0.0)
            elif hi - lo < 1e-12:
                row.append(1.0)
            else:
                row.append(_NADIR_FLOOR + span * (raw - lo) / (hi - lo))
        points.append(row)
    return points, bounds


def hypervolume(
    vectors: Sequence[UtilityVector],
    *,
    dims: Optional[Sequence[Objective]] = None,
    context: Optional[Sequence[UtilityVector]] = None,
    reference: float = 0.0,
    max_exact_points: int = 12,
    samples: int = 20000,
) -> float:
    """Hypervolume of the region dominated by ``vectors``, in normalized space.

    Pass ``context`` when comparing subsets of a larger candidate population
    so that all of them are normalized against the same bounds.

    Exact by inclusion–exclusion for small fronts; deterministic
    low-discrepancy sampling beyond that, so the number is reproducible
    across runs and machines.
    """
    if not vectors:
        return 0.0
    dim_source = list(context) if context else list(vectors)
    dim_list = list(dims) if dims else sorted(
        {d for v in dim_source for d in v.measured}, key=lambda d: d.value
    )
    if not dim_list:
        return 0.0

    points, _ = _normalize(vectors, dim_list, context=context)
    points = [p for p in points if all(x > reference for x in p)]
    if not points:
        return 0.0

    # Drop dominated points — they contribute nothing.
    keep: list[list[float]] = []
    for i, p in enumerate(points):
        if any(
            all(q[k] >= p[k] for k in range(len(p))) and any(q[k] > p[k] for k in range(len(p)))
            for j, q in enumerate(points)
            if i != j
        ):
            continue
        keep.append(p)
    points = keep

    if len(points) <= max_exact_points:
        return _hv_inclusion_exclusion(points, reference)
    return _hv_monte_carlo(points, reference, samples)


def _hv_inclusion_exclusion(points: list[list[float]], reference: float) -> float:
    total = 0.0
    n = len(points)
    for size in range(1, n + 1):
        sign = 1.0 if size % 2 == 1 else -1.0
        for combo in itertools.combinations(range(n), size):
            volume = 1.0
            for d in range(len(points[0])):
                edge = min(points[i][d] for i in combo) - reference
                if edge <= 0:
                    volume = 0.0
                    break
                volume *= edge
            total += sign * volume
    return max(0.0, total)


def _hv_monte_carlo(points: list[list[float]], reference: float, samples: int) -> float:
    dims = len(points[0])
    upper = [max(p[d] for p in points) for d in range(dims)]
    box = 1.0
    for d in range(dims):
        box *= max(0.0, upper[d] - reference)
    if box <= 0:
        return 0.0

    # Deterministic low-discrepancy sampling (additive recurrence / Kronecker),
    # so the estimate is identical across runs and machines.
    alphas = [_plastic_alpha(d + 1, dims) for d in range(dims)]
    hits = 0
    for i in range(1, samples + 1):
        sample = [reference + ((i * alphas[d]) % 1.0) * (upper[d] - reference) for d in range(dims)]
        if any(all(p[d] >= sample[d] for d in range(dims)) for p in points):
            hits += 1
    return box * hits / samples


def _plastic_alpha(index: int, dims: int) -> float:
    """Irrational rotation constants from the generalized plastic number."""
    g = 2.0
    for _ in range(24):
        g = (1 + g) ** (1.0 / (dims + 1))
    return (1.0 / g) ** index % 1.0


# ---------------------------------------------------------------------------
# Selection from the Pareto set
# ---------------------------------------------------------------------------


class SelectionPolicy(BaseModel):
    """How a single workflow is chosen out of the non-dominated set.

    Recorded in the run manifest so the trade-off is visible rather than
    hidden inside a scoring function.
    """

    model_config = ConfigDict(extra="forbid")

    #: ``lexicographic`` | ``weighted`` | ``constrained`` | ``expert``
    mode: str = "lexicographic"
    #: Priority order for lexicographic mode.
    priority: list[Objective] = Field(
        default_factory=lambda: [
            Objective.VALIDITY,
            Objective.EVIDENCE,
            Objective.SCIENTIFIC_UTILITY,
            Objective.ROBUSTNESS,
            Objective.COST,
            Objective.LATENCY,
            Objective.RISK,
        ]
    )
    #: Weights for weighted mode. Only used when the user asks for it.
    weights: dict[Objective, float] = Field(default_factory=dict)
    #: Hard floors, e.g. ``{"validity": 1.0}`` in constrained mode.
    floors: dict[Objective, float] = Field(default_factory=dict)
    #: Tolerance for treating two lexicographic values as tied.
    epsilon: float = 1e-6


def select(
    candidates: Sequence[tuple[str, UtilityVector]],
    policy: SelectionPolicy,
) -> tuple[Optional[str], list[str], str]:
    """Return ``(chosen_id, pareto_front_ids, rationale)``.

    Candidates failing a declared floor are excluded before selection, and the
    rationale states exactly why the winner won.
    """
    if not candidates:
        return None, [], "no candidates"

    admissible = list(candidates)
    excluded: list[str] = []
    for objective, floor in policy.floors.items():
        kept = []
        for name, vec in admissible:
            value = vec.get(objective)
            if value is None or value < floor:
                excluded.append(f"{name} (floor {objective.value} < {floor})")
            else:
                kept.append((name, vec))
        admissible = kept
    if not admissible:
        return None, [], f"all candidates excluded by floors: {'; '.join(excluded)}"

    front = pareto_front(admissible)
    front_set = {name for name in front}
    front_candidates = [(n, v) for n, v in admissible if n in front_set]

    if len(front_candidates) == 1:
        return (
            front_candidates[0][0],
            front,
            f"single non-dominated candidate ({front_candidates[0][0]})",
        )

    if policy.mode == "weighted" and policy.weights:
        def score(item: tuple[str, UtilityVector]) -> float:
            _, vec = item
            return sum(
                weight * (vec.oriented(obj) or 0.0) for obj, weight in policy.weights.items()
            )

        best = max(front_candidates, key=score)
        return best[0], front, "weighted scalarization over the Pareto front (weights declared by user)"

    if policy.mode == "expert":
        return None, front, "expert pairwise judgment required; Pareto front returned unselected"

    # Lexicographic is the default: no invented exchange rates between axes.
    ordered = list(front_candidates)
    for objective in policy.priority:
        values = [(name, vec.oriented(objective)) for name, vec in ordered]
        measured = [(n, v) for n, v in values if v is not None]
        if not measured:
            continue
        best_value = max(v for _, v in measured)
        survivors = {n for n, v in measured if v >= best_value - policy.epsilon}
        ordered = [(n, v) for n, v in ordered if n in survivors]
        if len(ordered) == 1:
            return (
                ordered[0][0],
                front,
                f"lexicographic winner decided at objective '{objective.value}'",
            )
    chosen = ordered[0][0] if ordered else front_candidates[0][0]
    return chosen, front, "lexicographic order exhausted; first remaining candidate chosen"


# ---------------------------------------------------------------------------
# Synergy — is the multi-component workflow actually worth it?
# ---------------------------------------------------------------------------


class SynergyReport(BaseModel):
    """Whether composition beat the best honest single-component alternative.

    This is the number that answers "why not just use one strong agent" and
    it is reported even when it is negative.
    """

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    #: Per-objective margin over the best single-component baseline.
    margins: dict[Objective, float] = Field(default_factory=dict)
    #: Hypervolume of the composed workflow minus the best single baseline.
    hypervolume_synergy: float = 0.0
    best_baseline: Optional[str] = None
    dominated_by_baseline: bool = False
    verdict: str = ""


def synergy(
    workflow_id: str,
    workflow_utility: UtilityVector,
    baselines: Sequence[tuple[str, UtilityVector]],
) -> SynergyReport:
    """Compare a composed workflow against single-component baselines.

    ``baselines`` should include the strongest single component *and* an
    equal-budget single agent; otherwise the comparison flatters composition.
    """
    if not baselines:
        return SynergyReport(
            workflow_id=workflow_id,
            verdict="no baselines supplied; synergy is undefined",
        )

    # One shared normalization across the workflow and every baseline —
    # normalizing each in isolation would make them all score 1.0.
    context = [workflow_utility] + [vec for _, vec in baselines]
    hv_workflow = hypervolume([workflow_utility], context=context)
    best_name, best_hv = None, -math.inf
    for name, vec in baselines:
        hv = hypervolume([vec], context=context)
        if hv > best_hv:
            best_name, best_hv = name, hv

    best_vec = dict(baselines)[best_name] if best_name else None
    margins: dict[Objective, float] = {}
    if best_vec is not None:
        for dim in workflow_utility.comparable_with(best_vec):
            a = workflow_utility.oriented(dim)
            b = best_vec.oriented(dim)
            if a is not None and b is not None:
                margins[dim] = a - b

    dominated = bool(
        best_vec is not None and dominates(best_vec, workflow_utility)
    )
    hv_delta = hv_workflow - best_hv

    if dominated:
        verdict = (
            f"NEGATIVE: baseline '{best_name}' Pareto-dominates the composed workflow; "
            "composition is not justified for this task"
        )
    elif hv_delta > 0:
        verdict = f"positive synergy over best baseline '{best_name}'"
    else:
        verdict = f"no measurable synergy over best baseline '{best_name}'"

    return SynergyReport(
        workflow_id=workflow_id,
        margins=margins,
        hypervolume_synergy=hv_delta,
        best_baseline=best_name,
        dominated_by_baseline=dominated,
        verdict=verdict,
    )


__all__ = [
    "Objective",
    "MAXIMIZE",
    "UtilityVector",
    "dominates",
    "pareto_front",
    "hypervolume",
    "SelectionPolicy",
    "select",
    "SynergyReport",
    "synergy",
]
