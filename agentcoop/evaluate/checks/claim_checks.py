"""CLAIM-level checks: is the scientific conclusion actually supported?

This is the level most open-ended tasks have no oracle for, and pretending
otherwise is how a system ends up optimizing something that does not measure
the goal. These checks therefore never score the *truth* of a claim. They
score whether the claim is stated in a form a reviewer could audit: what it
rests on, what contradicts it, what else could explain the observation, and
how uncertain it is.

``ClaimBundle`` lives in :mod:`agentcoop.evaluate.claim`, which is owned by a
different part of the rebuild. It is imported lazily inside each function so
this module stays importable — and honestly reports ``UNAVAILABLE`` — before
that module exists, rather than exploding at import time and taking the whole
evaluation stack with it.
"""

from __future__ import annotations

from typing import Any

from agentcoop.evaluate.contract import (
    CheckContext,
    make_result,
    unavailable,
)
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus

_LEVEL = CheckLevel.CLAIM

#: Default artifact type name searched for a serialized claim bundle.
_DEFAULT_CLAIM_TYPE = "ClaimBundle"


class _NoBundle(Exception):
    """Raised internally when no claim bundle can be resolved."""

    def __init__(self, reason: str, **evidence: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


def _load_bundle(ctx: CheckContext) -> Any:
    """Resolve the run's :class:`ClaimBundle`, or explain why there is none.

    Resolution order, most explicit first:

    1. ``params['claim_bundle']`` — a bundle instance or a mapping;
    2. an artifact whose ``type_name`` matches ``params['claim_type']``
       (default ``ClaimBundle``), payload validated into the model;
    3. nothing → the check is unavailable.

    A malformed payload is *not* silently downgraded to "no bundle": it is a
    different fault (the workflow produced a claim artifact that is not a
    claim) and is reported as such by ``claim_bundle_wellformed``.
    """
    try:
        from agentcoop.evaluate.claim import ClaimBundle  # noqa: PLC0415 - see module docstring
    except ImportError as exc:  # pragma: no cover - depends on integration order
        raise _NoBundle(
            "agentcoop.evaluate.claim is not importable; claim-level evaluation "
            "cannot run",
            error=str(exc),
        ) from exc

    supplied = ctx.params.get("claim_bundle")
    if supplied is not None:
        if isinstance(supplied, ClaimBundle):
            return supplied
        return ClaimBundle.model_validate(supplied)

    claim_type = str(ctx.params.get("claim_type", _DEFAULT_CLAIM_TYPE))
    candidates = sorted(
        aid for aid, art in ctx.artifacts.items() if art.type_name == claim_type
    )
    if not candidates:
        raise _NoBundle(
            f"no claim bundle supplied and no artifact of type '{claim_type}' was produced",
            claim_type=claim_type,
        )
    payload = ctx.artifacts[candidates[0]].payload
    if isinstance(payload, ClaimBundle):
        return payload
    return ClaimBundle.model_validate(payload)


def _guarded(ctx: CheckContext):
    """Resolve the bundle, converting failures into honest check results."""
    try:
        return _load_bundle(ctx), None
    except _NoBundle as exc:
        return None, unavailable(_LEVEL, exc.reason, subject_kind="claim", **exc.evidence)
    except Exception as exc:  # noqa: BLE001 - malformed payload is a real verdict
        return None, make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"claim artifact is not a well-formed ClaimBundle: {type(exc).__name__}",
            subject_kind="claim",
            score=0.0,
            evidence={"error": f"{type(exc).__name__}: {exc}"},
        )


def claim_bundle_wellformed(ctx: CheckContext) -> CheckResult:
    """The conclusion is a structured bundle, not a paragraph of prose.

    Required for a claim to be auditable at all: a non-empty claim, at least
    one piece of supporting evidence, and a non-empty provenance list. A
    bundle that asserts a conclusion with an empty ``supporting_evidence``
    list is a model talking, which is exactly the thing that must not be
    load-bearing.
    """
    bundle, failure = _guarded(ctx)
    if failure is not None:
        return failure

    problems: list[str] = []
    if not str(getattr(bundle, "claim", "")).strip():
        problems.append("claim text is empty")
    if not getattr(bundle, "supporting_evidence", None):
        problems.append("no supporting evidence cited")
    if not getattr(bundle, "provenance", None):
        problems.append("no provenance artifact ids recorded")

    n_required = 3
    if problems:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "claim bundle is not auditable: " + "; ".join(problems),
            score=1.0 - len(problems) / n_required,
            subject_kind="claim",
            evidence={"problems": problems},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        (
            f"claim bundle cites {len(bundle.supporting_evidence)} supporting item(s) "
            f"and {len(bundle.provenance)} provenance artifact(s)"
        ),
        score=1.0,
        subject_kind="claim",
        evidence={
            "n_supporting": len(bundle.supporting_evidence),
            "n_contradicting": len(getattr(bundle, "contradicting_evidence", []) or []),
            "n_provenance": len(bundle.provenance),
        },
    )


def claim_traceable(ctx: CheckContext) -> CheckResult:
    """Provenance resolves, and covers the artifact types the task demands.

    ``EvidenceChainRequirement.must_trace_to`` names the artifact types any
    claim has to rest on. A claim citing artifact ids that are not in the run
    is worse than one citing none: it looks traceable and is not.
    """
    bundle, failure = _guarded(ctx)
    if failure is not None:
        return failure

    provenance = [str(a) for a in (getattr(bundle, "provenance", None) or [])]
    required_types = sorted(
        {t for req in ctx.dossier.evidence_chain for t in req.must_trace_to}
    )

    if not provenance:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "claim records no provenance; it is not traceable to any artifact",
            score=0.0,
            subject_kind="claim",
            evidence={"required_types": required_types},
        )

    if not ctx.artifacts:
        return unavailable(
            _LEVEL,
            "no artifacts recorded; claim provenance cannot be resolved",
            subject_kind="claim",
            provenance=provenance,
        )

    unresolved = sorted(a for a in provenance if a not in ctx.artifacts)
    reached_types = sorted(
        {ctx.artifacts[a].type_name for a in provenance if a in ctx.artifacts}
    )

    missing_types = sorted(set(required_types) - set(reached_types))
    problems: list[str] = []
    if unresolved:
        problems.append(f"{len(unresolved)} provenance id(s) do not resolve to a run artifact")
    if missing_types:
        problems.append("claim does not trace to required type(s): " + ", ".join(missing_types))

    if problems:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "; ".join(problems),
            score=(
                len(set(reached_types) & set(required_types)) / len(required_types)
                if required_types
                else 0.0
            ),
            subject_kind="claim",
            evidence={
                "unresolved_provenance": unresolved,
                "reached_types": reached_types,
                "required_types": required_types,
                "missing_types": missing_types,
            },
        )

    if not required_types:
        # Provenance resolves, but the dossier never said what a claim must
        # rest on. Resolvable-but-unconstrained is weaker evidence than
        # "traced to the required types", so it is reported as a warning
        # rather than a clean pass.
        return make_result(
            _LEVEL,
            CheckStatus.WARN,
            (
                f"all {len(provenance)} provenance id(s) resolve, but the dossier "
                "declares no evidence-chain requirement to check them against"
            ),
            score=1.0,
            subject_kind="claim",
            evidence={"reached_types": reached_types, "required_types": []},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        (
            f"claim traces to {len(provenance)} resolvable artifact(s) covering all "
            f"{len(required_types)} required type(s)"
        ),
        score=1.0,
        subject_kind="claim",
        evidence={"reached_types": reached_types, "required_types": required_types},
    )


def claim_has_alternatives(ctx: CheckContext) -> CheckResult:
    """Competing explanations were considered and written down.

    A conclusion offered without alternatives is not a scientific conclusion,
    it is an assertion. ``params['min_alternatives']`` (default 1) sets the
    bar; contradicting evidence counts toward it because "here is what argues
    against this" is the same epistemic act.
    """
    bundle, failure = _guarded(ctx)
    if failure is not None:
        return failure

    minimum = int(ctx.params.get("min_alternatives", 1))
    alternatives = [
        str(a).strip()
        for a in (getattr(bundle, "alternative_explanations", None) or [])
        if str(a).strip()
    ]
    contradicting = list(getattr(bundle, "contradicting_evidence", None) or [])
    considered = len(alternatives) + len(contradicting)

    if considered < minimum:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"claim considers {considered} alternative explanation(s) or "
                f"contradicting item(s), below the required {minimum}"
            ),
            score=considered / minimum if minimum else 0.0,
            subject_kind="claim",
            evidence={
                "alternative_explanations": alternatives,
                "n_contradicting": len(contradicting),
                "min_alternatives": minimum,
            },
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        (
            f"claim records {len(alternatives)} alternative explanation(s) and "
            f"{len(contradicting)} contradicting item(s)"
        ),
        score=1.0,
        subject_kind="claim",
        evidence={
            "alternative_explanations": alternatives,
            "n_contradicting": len(contradicting),
        },
    )


def claim_uncertainty_declared(ctx: CheckContext) -> CheckResult:
    """Uncertainty and assumptions are stated, not left to the reader.

    ``IRREDUCIBLE_UNCERTAINTY`` is a first-class fault class in this system:
    sometimes the right output is "the data do not determine the answer".
    That is only a legitimate result if the bundle says so explicitly, so a
    blank ``uncertainty`` field fails here rather than being read as
    confidence.
    """
    bundle, failure = _guarded(ctx)
    if failure is not None:
        return failure

    uncertainty = str(getattr(bundle, "uncertainty", "") or "").strip()
    assumptions = [
        str(a).strip() for a in (getattr(bundle, "assumptions", None) or []) if str(a).strip()
    ]
    require_assumptions = bool(ctx.params.get("require_assumptions", True))

    problems: list[str] = []
    if not uncertainty:
        problems.append("uncertainty is not declared")
    if require_assumptions and not assumptions:
        problems.append("no assumptions are recorded")

    if problems:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "; ".join(problems),
            score=0.0 if not uncertainty else 0.5,
            subject_kind="claim",
            evidence={
                "uncertainty": uncertainty,
                "assumptions": assumptions,
                "problems": problems,
            },
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"uncertainty declared and {len(assumptions)} assumption(s) recorded",
        score=1.0,
        subject_kind="claim",
        evidence={"uncertainty": uncertainty, "assumptions": assumptions},
    )


__all__ = [
    "claim_bundle_wellformed",
    "claim_traceable",
    "claim_has_alternatives",
    "claim_uncertainty_declared",
]
