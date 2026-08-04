"""RESOURCE-level checks: cost, time, and reproducibility.

Two determinism constraints shape this module:

* every number comes from the **recorded** :class:`CostProfile`, never from
  ``time.time()``. Re-evaluating a stored run manifest a week later has to
  produce the same verdict it did during the run, and a wall-clock read in a
  decision path would break that;
* reproducibility is only ever asserted from **recorded repeat runs**. A
  component declaring itself deterministic is documentation, not evidence,
  and documentation is not load-bearing anywhere in this system.

An undeclared limit is reported as unmeasured rather than satisfied. "We
never set a budget" and "we stayed inside the budget" are different facts,
and only one of them is a result.
"""

from __future__ import annotations

from typing import Any, Optional

from agentcoop.evaluate.contract import (
    CheckContext,
    make_result,
    trace_node_results,
    unavailable,
)
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus, SignalSource

_LEVEL = CheckLevel.RESOURCE


def _ratio(spent: float, limit: float) -> float:
    """Headroom in ``[0, 1]``: 1.0 spent nothing, 0.0 spent the whole budget."""
    if limit <= 0:
        return 0.0 if spent > 0 else 1.0
    return max(0.0, min(1.0, 1.0 - spent / limit))


def within_budget(ctx: CheckContext) -> CheckResult:
    """Recorded spend stayed inside every declared resource limit.

    Covers money, tokens, and component calls. Limits left at ``None`` are
    skipped; if *no* limit is declared at all the check is unavailable,
    because a resource contract with no limits is an absent contract rather
    than a satisfied one.
    """
    limits = ctx.dossier.limits
    declared: dict[str, tuple[float, float]] = {}

    if limits.max_usd is not None:
        declared["usd"] = (float(ctx.cost.usd), float(limits.max_usd))
    if limits.max_tokens is not None:
        declared["tokens"] = (float(ctx.cost.tokens), float(limits.max_tokens))
    if limits.max_component_calls is not None:
        node_results = trace_node_results(ctx.trace)
        if node_results:
            declared["component_calls"] = (
                float(len(node_results)),
                float(limits.max_component_calls),
            )

    if not declared:
        return unavailable(
            _LEVEL,
            (
                "the dossier declares no monetary, token, or call limit that can be "
                "compared against recorded spend"
            ),
        )

    exceeded = {k: v for k, v in declared.items() if v[0] > v[1]}
    headroom = min(_ratio(spent, limit) for spent, limit in declared.values())
    evidence: dict[str, Any] = {
        "spent": {k: v[0] for k, v in sorted(declared.items())},
        "limits": {k: v[1] for k, v in sorted(declared.items())},
    }

    if exceeded:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "budget exceeded on: "
            + ", ".join(
                f"{k} ({v[0]:g} > {v[1]:g})" for k, v in sorted(exceeded.items())
            ),
            score=headroom,
            evidence={**evidence, "exceeded": sorted(exceeded)},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"within all {len(declared)} declared budget(s); tightest headroom {headroom:.2f}",
        score=headroom,
        evidence=evidence,
    )


def within_wall_time(ctx: CheckContext) -> CheckResult:
    """Recorded latency stayed inside the declared wall-time limit.

    Uses ``CostProfile.latency_s`` accumulated during the run rather than
    reading the clock here, so the verdict is a property of the run and not
    of when the report is generated.
    """
    limit = ctx.dossier.limits.max_wall_time_s
    if limit is None:
        return unavailable(
            _LEVEL,
            "the dossier declares no wall-time limit; runtime is unconstrained and unverified",
            recorded_latency_s=float(ctx.cost.latency_s),
        )

    spent = float(ctx.cost.latency_s)
    if spent > float(limit):
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"recorded latency {spent:g}s exceeds the declared limit {float(limit):g}s",
            score=_ratio(spent, float(limit)),
            evidence={"latency_s": spent, "max_wall_time_s": float(limit)},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"recorded latency {spent:g}s within the declared limit {float(limit):g}s",
        score=_ratio(spent, float(limit)),
        evidence={"latency_s": spent, "max_wall_time_s": float(limit)},
    )


def _current_hashes(ctx: CheckContext) -> dict[str, str]:
    return {
        aid: (art.content_hash or art.compute_hash())
        for aid, art in sorted(ctx.artifacts.items())
    }


def reproducible(ctx: CheckContext) -> CheckResult:
    """Repeated execution under the same seed produced identical artifacts.

    Evidence must come from a real repeat:

    * ``params['repeat_hashes']`` — two or more ``{artifact_id: hash}`` maps
      from separate runs; or
    * ``params['baseline_hashes']`` — a recorded map compared against the
      artifacts in this context.

    With neither, the check is unavailable. A ``BehaviorContract`` that claims
    ``deterministic=True`` is deliberately *not* accepted as evidence here:
    that field is populated by the determinism probe, and quoting it back
    would turn one observation into two, inflating apparent coverage.
    """
    runs: list[dict[str, str]] = []
    for raw in ctx.params.get("repeat_hashes") or []:
        if isinstance(raw, dict):
            runs.append({str(k): str(v) for k, v in raw.items()})

    baseline = ctx.params.get("baseline_hashes")
    if isinstance(baseline, dict) and ctx.artifacts:
        runs = [{str(k): str(v) for k, v in baseline.items()}, _current_hashes(ctx)]

    if len(runs) < 2:
        return unavailable(
            _LEVEL,
            (
                "no repeat run recorded; reproducibility is unestablished "
                "(a component declaring itself deterministic is not evidence)"
            ),
            source=SignalSource.STATISTICAL,
            n_recorded_runs=len(runs),
        )

    shared = set(runs[0])
    for run in runs[1:]:
        shared &= set(run)
    if not shared:
        return unavailable(
            _LEVEL,
            "recorded repeat runs share no artifact id; they cannot be compared",
            source=SignalSource.STATISTICAL,
            n_recorded_runs=len(runs),
        )

    differing = sorted(
        aid for aid in shared if len({run[aid] for run in runs}) > 1
    )
    only_in_some = sorted(set().union(*(set(r) for r in runs)) - shared)

    if differing:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(differing)} of {len(shared)} artifact(s) differ across "
                f"{len(runs)} repeat run(s)"
            ),
            source=SignalSource.STATISTICAL,
            score=1.0 - len(differing) / len(shared),
            subject=differing[0],
            subject_kind="artifact",
            evidence={
                "differing_artifacts": differing,
                "n_runs": len(runs),
                "artifacts_missing_from_some_run": only_in_some,
            },
        )

    if only_in_some:
        return make_result(
            _LEVEL,
            CheckStatus.WARN,
            (
                f"{len(shared)} shared artifact(s) reproduced exactly, but "
                f"{len(only_in_some)} artifact(s) appear in only some runs"
            ),
            source=SignalSource.STATISTICAL,
            score=len(shared) / (len(shared) + len(only_in_some)),
            evidence={
                "n_runs": len(runs),
                "artifacts_missing_from_some_run": only_in_some,
            },
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(shared)} artifact(s) reproduced exactly across {len(runs)} run(s)",
        source=SignalSource.STATISTICAL,
        score=1.0,
        evidence={"n_runs": len(runs), "n_artifacts": len(shared)},
    )


__all__ = ["within_budget", "within_wall_time", "reproducible"]
