"""ARTIFACT-level checks: is what flowed between components actually usable?

Heterogeneous scientific components rarely fail by disagreeing about JSON.
They fail because one side emitted Ensembl ids and the other assumed HGNC
symbols, or because a converter silently dropped 40% of the rows, or because
a node returned an empty result with exit code 0 and every downstream check
happily validated the empty envelope.

So the rule these checks enforce is: an *undeclared* semantic facet is a
failure, not a pass. ``UNDERSPECIFIED`` compatibility means we cannot prove
the handoff is correct, and refusing to assume it is the whole point.
"""

from __future__ import annotations

from typing import Any, Optional

from agentcoop.evaluate.contract import (
    CheckContext,
    content_fields,
    make_result,
    payload_is_empty,
    result_ok,
    trace_node_results,
    unavailable,
)
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus

_LEVEL = CheckLevel.ARTIFACT


def _node_to_subgoal(ctx: CheckContext) -> dict[str, str]:
    """``{node_id: subgoal_id}`` from the compiled workflow, or ``{}``."""
    if ctx.workflow is None:
        return {}
    mapping: dict[str, str] = {}
    for node in ctx.workflow.graph().nodes:
        if node.subgoal_id:
            mapping[node.node_id] = node.subgoal_id
    return mapping


def facets_declared(ctx: CheckContext) -> CheckResult:
    """Each artifact declares every facet its own type marks as required.

    ``ArtifactType.required_facets`` lists the keys a *consumer* must have
    pinned down to interpret the payload. An artifact that omits one is not
    "probably fine" — it is uninterpretable, and the compiler is forbidden
    from bridging it on a guess.
    """
    if not ctx.artifacts:
        return unavailable(_LEVEL, "no artifacts to inspect")

    missing: dict[str, list[str]] = {}
    checked = 0
    untyped: list[str] = []
    for artifact_id in sorted(ctx.artifacts):
        artifact = ctx.artifacts[artifact_id]
        artifact_type = ctx.dossier.artifact_type(artifact.type_name)
        if artifact_type is None:
            untyped.append(artifact_id)
            continue
        if not artifact_type.required_facets:
            continue
        checked += 1
        absent = sorted(set(artifact_type.required_facets) - set(artifact.facets))
        if absent:
            missing[artifact_id] = absent

    if checked == 0:
        return unavailable(
            _LEVEL,
            (
                "no artifact type declares required facets; the semantic contract "
                "of every handoff is unspecified"
            ),
            untyped_artifacts=untyped,
        )

    if missing:
        first = sorted(missing)[0]
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"{len(missing)} of {checked} artifact(s) omit a required facet",
            score=1.0 - len(missing) / checked,
            subject=first,
            subject_kind="artifact",
            evidence={"missing_facets": missing, "untyped_artifacts": untyped},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"{checked} artifact(s) declare every facet their type requires",
        score=1.0,
        evidence={"n_checked": checked, "untyped_artifacts": untyped},
    )


def facet_match(ctx: CheckContext) -> CheckResult:
    """Produced artifacts carry the facet values their subgoal demanded.

    Two distinct failure modes, both reported as ``FAIL``:

    ``conflict``
        the artifact declares ``namespace=ENSEMBL`` where the subgoal
        requires ``HGNC``;
    ``underspecified``
        the artifact declares no ``namespace`` at all.

    The second is the one systems get wrong. Treating a missing facet as
    satisfied is precisely the assumption that produces a silently wrong
    join three nodes later.
    """
    # (subgoal_id, artifact type, required facet values), sorted for determinism.
    pairs: list[tuple[str, str, dict[str, str]]] = []
    for subgoal in ctx.dossier.subgoals:
        for type_name in sorted(subgoal.required_output_facets):
            facets = subgoal.required_output_facets[type_name]
            if facets:
                pairs.append((subgoal.subgoal_id, type_name, dict(facets)))

    if not pairs:
        return unavailable(
            _LEVEL,
            (
                "no subgoal declares required output facets; semantic correctness "
                "of the outputs is unverified"
            ),
        )
    if not ctx.artifacts:
        return unavailable(_LEVEL, "no artifacts to inspect")

    node_subgoal = _node_to_subgoal(ctx)
    conflicts: dict[str, dict[str, list[str]]] = {}
    underspecified: dict[str, list[str]] = {}
    matched = 0
    unmatched_requirements: list[str] = []

    for subgoal_id, type_name, wanted in pairs:
        candidates = [
            aid
            for aid, art in sorted(ctx.artifacts.items())
            if art.type_name == type_name
            and (not node_subgoal or node_subgoal.get(art.producer, subgoal_id) == subgoal_id)
        ]
        if not candidates:
            unmatched_requirements.append(f"{subgoal_id}:{type_name}")
            continue
        for aid in candidates:
            artifact = ctx.artifacts[aid]
            matched += 1
            for key in sorted(wanted):
                have = artifact.facets.get(key)
                if have is None:
                    underspecified.setdefault(aid, []).append(key)
                elif have != wanted[key]:
                    conflicts.setdefault(aid, {})[key] = [have, wanted[key]]

    if matched == 0:
        return unavailable(
            _LEVEL,
            (
                "no produced artifact matches a subgoal that declares required "
                "output facets"
            ),
            unmatched_requirements=sorted(unmatched_requirements),
        )

    if conflicts or underspecified:
        offenders = sorted(set(conflicts) | set(underspecified))
        parts: list[str] = []
        if conflicts:
            parts.append(f"{len(conflicts)} facet conflict(s)")
        if underspecified:
            parts.append(f"{len(underspecified)} artifact(s) with undeclared facets")
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "; ".join(parts) + " against declared subgoal requirements",
            score=1.0 - len(offenders) / matched,
            subject=offenders[0],
            subject_kind="artifact",
            evidence={
                "conflicting_facets": conflicts,
                "underspecified_facets": {k: sorted(v) for k, v in underspecified.items()},
                "unmatched_requirements": sorted(unmatched_requirements),
            },
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"{matched} artifact(s) carry the facet values their subgoal requires",
        score=1.0,
        evidence={
            "n_matched": matched,
            "unmatched_requirements": sorted(unmatched_requirements),
        },
    )


def _declared_coverage(artifact: Artifact, key: str) -> Optional[float]:
    """Coverage a converter/adapter recorded on the artifact, if any."""
    if isinstance(artifact.payload, dict) and key in artifact.payload:
        raw: Any = artifact.payload[key]
    elif key in artifact.facets:
        raw = artifact.facets[key]
    else:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def coverage_threshold(ctx: CheckContext) -> CheckResult:
    """Facet conversions retained enough of the data to be trustworthy.

    A registered ``Converter`` declares an ``expected_coverage``; a real
    conversion reports what it actually realized. An id mapping that survives
    only 55% of the rows has quietly changed the population under study, and
    no downstream check will notice.

    If nothing recorded a coverage measurement the check is unavailable —
    "we never measured the loss" is not "there was no loss".
    """
    key = str(ctx.params.get("coverage_key", "coverage"))
    threshold = float(ctx.params.get("min_coverage", 0.9))
    if not ctx.artifacts:
        return unavailable(_LEVEL, "no artifacts to inspect")

    measured: dict[str, float] = {}
    for artifact_id in sorted(ctx.artifacts):
        value = _declared_coverage(ctx.artifacts[artifact_id], key)
        if value is not None:
            measured[artifact_id] = value

    if not measured:
        return unavailable(
            _LEVEL,
            (
                f"no artifact declares a '{key}' measurement; conversion loss is "
                "unmeasured"
            ),
            coverage_key=key,
            min_coverage=threshold,
        )

    below = {aid: v for aid, v in measured.items() if v < threshold}
    worst = min(measured.values())
    if below:
        offender = sorted(below, key=lambda a: (below[a], a))[0]
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(below)} of {len(measured)} artifact(s) below the coverage "
                f"threshold {threshold} (worst {worst:.3f})"
            ),
            score=worst,
            subject=offender,
            subject_kind="artifact",
            evidence={"coverage": measured, "min_coverage": threshold, "below": below},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"{len(measured)} measured artifact(s) at or above coverage {threshold}",
        score=worst,
        evidence={"coverage": measured, "min_coverage": threshold},
    )


def provenance_complete(ctx: CheckContext) -> CheckResult:
    """Every derived artifact names its producer and resolvable ancestors.

    Localization is backward slicing over ``derived_from``. A break in that
    chain does not merely lose an audit trail — it makes it impossible to
    tell whether a downstream node produced garbage or faithfully consumed
    garbage, which is exactly the attribution error v1 made.
    """
    if not ctx.artifacts:
        return unavailable(_LEVEL, "no artifacts to inspect")

    provided = set(ctx.dossier.provided_inputs)
    no_producer: list[str] = []
    dangling: dict[str, list[str]] = {}
    checked = 0

    for artifact_id in sorted(ctx.artifacts):
        artifact = ctx.artifacts[artifact_id]
        if artifact.type_name in provided and not artifact.producer:
            # A task input legitimately has no producing node.
            continue
        checked += 1
        if not artifact.producer:
            no_producer.append(artifact_id)
        missing = sorted(a for a in artifact.derived_from if a not in ctx.artifacts)
        if missing:
            dangling[artifact_id] = missing

    if checked == 0:
        return unavailable(
            _LEVEL,
            "every artifact is a declared task input; no provenance to verify",
        )

    broken = sorted(set(no_producer) | set(dangling))
    if broken:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(broken)} of {checked} artifact(s) have incomplete provenance "
                f"({len(no_producer)} without a producer, {len(dangling)} with "
                "unresolvable ancestors)"
            ),
            score=1.0 - len(broken) / checked,
            subject=broken[0],
            subject_kind="artifact",
            evidence={"no_producer": no_producer, "dangling_ancestors": dangling},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"{checked} artifact(s) name a producer and resolvable ancestors",
        score=1.0,
        evidence={"n_checked": checked},
    )


def no_silent_empty(ctx: CheckContext) -> CheckResult:
    """No node reported success while emitting an empty artifact.

    An empty result can be a real finding ("no genes passed the threshold"),
    so this check does not forbid emptiness — it forbids *undeclared*
    emptiness. A node that means it says so, via a note on the artifact
    (``params['declaration_note']``, default ``empty_result_declared``).
    Anything else is a component returning nothing while claiming success,
    which is the most dangerous failure mode in the taxonomy because every
    downstream check still passes.
    """
    if not ctx.artifacts:
        return unavailable(_LEVEL, "no artifacts to inspect")

    note = str(ctx.params.get("declaration_note", "empty_result_declared"))
    overrides = ctx.params.get("payload_fields")
    node_results = trace_node_results(ctx.trace)

    offenders: dict[str, dict[str, Any]] = {}
    declared_empty: list[str] = []
    for artifact_id in sorted(ctx.artifacts):
        artifact = ctx.artifacts[artifact_id]
        fields = content_fields(ctx.dossier, artifact.type_name, overrides)
        if not payload_is_empty(artifact.payload, fields=fields):
            continue
        if note in artifact.notes:
            declared_empty.append(artifact_id)
            continue
        producer_ok = result_ok(node_results.get(artifact.producer))
        if producer_ok is False:
            # The node already admitted failure; that is loud, not silent, and
            # ``exit_status`` owns it. Reporting it twice would double-count
            # one fault against two levels.
            continue
        offenders[artifact_id] = {
            "producer": artifact.producer,
            "type": artifact.type_name,
            "producer_reported_ok": producer_ok,
        }

    if offenders:
        first = sorted(offenders)[0]
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(offenders)} artifact(s) are empty while their producer "
                "did not report failure"
            ),
            score=1.0 - len(offenders) / len(ctx.artifacts),
            subject=first,
            subject_kind="artifact",
            evidence={"silent_empty": offenders, "declared_empty": declared_empty},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        (
            f"no undeclared empty artifact among {len(ctx.artifacts)} "
            f"({len(declared_empty)} declared empty)"
        ),
        score=1.0,
        evidence={"declared_empty": declared_empty, "n_artifacts": len(ctx.artifacts)},
    )


__all__ = [
    "facets_declared",
    "facet_match",
    "coverage_threshold",
    "provenance_complete",
    "no_silent_empty",
]
