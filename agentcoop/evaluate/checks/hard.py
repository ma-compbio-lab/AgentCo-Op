"""HARD-level checks: did the machinery do what it literally promised?

These are the cheapest and most reliable checks in the stack, and they are
also the ones most often over-read. Passing every check here means the
processes exited zero, the payloads matched their schemas, and the required
artifact types exist. It does *not* mean the science is right — that is what
the four levels above exist for. Keeping the levels separate is what stops
"exit code 0" from being reported as success.
"""

from __future__ import annotations

from typing import Any, Optional

from agentcoop.evaluate.contract import (
    CheckContext,
    CheckFn,
    CheckLookup,
    content_fields,
    make_result,
    payload_is_empty,
    result_errors,
    result_exit_code,
    result_ok,
    trace_node_results,
    unavailable,
)
from agentcoop.ir.artifacts import validate_payload
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus

_LEVEL = CheckLevel.HARD


def exit_status(ctx: CheckContext) -> CheckResult:
    """Every executed node reported success and a zero/absent exit code.

    Requires a trace: without recorded node results there is nothing to read,
    and "no failures were recorded" is not evidence of "no failures occurred".
    """
    results = trace_node_results(ctx.trace)
    if not results:
        return unavailable(
            _LEVEL,
            "no node results recorded; process exit status is unobserved",
        )

    failed: list[str] = []
    unknown: list[str] = []
    detail: dict[str, Any] = {}
    for node_id in sorted(results):
        res = results[node_id]
        ok = result_ok(res)
        code = result_exit_code(res)
        if ok is None:
            unknown.append(node_id)
            continue
        if not ok or (code is not None and code != 0):
            failed.append(node_id)
            detail[node_id] = {
                "ok": ok,
                "exit_code": code,
                "errors": result_errors(res)[:5],
            }

    n_known = len(results) - len(unknown)
    if n_known == 0:
        return unavailable(
            _LEVEL,
            "recorded node results do not report success status",
            unknown_nodes=unknown,
        )

    if failed:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"{len(failed)} of {n_known} node(s) failed: {', '.join(failed)}",
            score=1.0 - len(failed) / n_known,
            subject=failed[0],
            subject_kind="node",
            evidence={"failed_nodes": failed, "detail": detail, "unknown_nodes": unknown},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {n_known} executed node(s) reported success",
        score=1.0,
        evidence={"n_nodes": n_known, "unknown_nodes": unknown},
    )


def schema_valid(ctx: CheckContext) -> CheckResult:
    """Artifact payloads conform to the structural schema of their declared type.

    Artifacts whose type is not declared in the dossier cannot be validated.
    They are counted as *unverified*, not as valid — if nothing at all could
    be validated the check is unavailable rather than a pass.
    """
    if not ctx.artifacts:
        return unavailable(_LEVEL, "no artifacts to validate")

    problems: dict[str, list[str]] = {}
    unverifiable: list[str] = []
    validated = 0
    for artifact_id in sorted(ctx.artifacts):
        artifact = ctx.artifacts[artifact_id]
        artifact_type = ctx.dossier.artifact_type(artifact.type_name)
        if artifact_type is None or not artifact_type.json_schema:
            unverifiable.append(artifact_id)
            continue
        validated += 1
        found = validate_payload(artifact.payload, artifact_type)
        if found:
            problems[artifact_id] = found

    if validated == 0:
        return unavailable(
            _LEVEL,
            "no artifact has a declared type with a structural schema",
            unverifiable_artifacts=unverifiable,
        )

    if problems:
        first = sorted(problems)[0]
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"{len(problems)} of {validated} validated artifact(s) violate their schema",
            score=1.0 - len(problems) / validated,
            subject=first,
            subject_kind="artifact",
            evidence={"problems": problems, "unverifiable_artifacts": unverifiable},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"{validated} artifact(s) conform to their declared schema",
        score=1.0,
        evidence={"n_validated": validated, "unverifiable_artifacts": unverifiable},
    )


def required_outputs_present(ctx: CheckContext) -> CheckResult:
    """Every artifact type the task requires was actually produced."""
    required = [str(x) for x in (ctx.params.get("required") or ctx.dossier.required_outputs)]
    if not required:
        return unavailable(
            _LEVEL,
            "the dossier declares no required outputs; completion is undefined",
        )
    if not ctx.artifacts:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"no artifacts produced; {len(required)} required output(s) missing",
            score=0.0,
            evidence={"required": sorted(required), "missing": sorted(required)},
        )

    produced = {a.type_name for a in ctx.artifacts.values()}
    missing = sorted(set(required) - produced)
    if missing:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"required output(s) missing: {', '.join(missing)}",
            score=1.0 - len(missing) / len(set(required)),
            subject=missing[0],
            subject_kind="artifact",
            evidence={"required": sorted(set(required)), "missing": missing},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(set(required))} required output type(s) present",
        score=1.0,
        evidence={"required": sorted(set(required))},
    )


def no_empty_result(ctx: CheckContext) -> CheckResult:
    """Required outputs exist *and* carry content.

    Separate from :func:`required_outputs_present` on purpose. An artifact of
    the right type whose payload is ``{"genes": []}`` satisfies presence and
    fails here, and conflating the two is exactly how a pipeline reports
    success after producing nothing.
    """
    required = [str(x) for x in (ctx.params.get("required") or ctx.dossier.required_outputs)]
    if not required:
        return unavailable(
            _LEVEL,
            "the dossier declares no required outputs; emptiness is undefined",
        )

    overrides = ctx.params.get("payload_fields")
    by_type: dict[str, list[str]] = {}
    for artifact_id in sorted(ctx.artifacts):
        by_type.setdefault(ctx.artifacts[artifact_id].type_name, []).append(artifact_id)

    empty: dict[str, list[str]] = {}
    absent: list[str] = []
    for type_name in sorted(set(required)):
        ids = by_type.get(type_name, [])
        if not ids:
            absent.append(type_name)
            continue
        fields = content_fields(ctx.dossier, type_name, overrides)
        blanks = [
            aid
            for aid in ids
            if payload_is_empty(ctx.artifacts[aid].payload, fields=fields)
        ]
        # A required output is empty only when *every* instance of it is
        # empty; a fan-out that produced one populated result still delivered.
        if len(blanks) == len(ids):
            empty[type_name] = blanks

    if absent or empty:
        broken = sorted(set(absent) | set(empty))
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            "required output(s) missing or empty: " + ", ".join(broken),
            score=1.0 - len(broken) / len(set(required)),
            subject=broken[0],
            subject_kind="artifact",
            evidence={"absent_types": sorted(absent), "empty_artifacts": empty},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(set(required))} required output type(s) carry content",
        score=1.0,
        evidence={"required": sorted(set(required))},
    )


def make_invariant_check(registry: CheckLookup) -> CheckFn:
    """Build the ``invariant`` dispatcher, closed over the registry.

    Invariants are declared in the dossier as ``(description, check, params)``.
    The dispatcher resolves ``Invariant.check`` through the registry instead of
    hard-coding a table, so a task can enforce a domain-specific hard
    constraint without touching this package.

    Three ways an invariant fails to be *evaluated*, all of which report
    ``UNAVAILABLE`` on a blocking check rather than quietly passing:

    * the invariant declares no executable check (a specification defect the
      dossier already reports — this makes it visible in the run report too);
    * the named implementation is not registered;
    * the invariant id does not resolve.
    """

    def invariant(ctx: CheckContext) -> CheckResult:
        invariant_id = ctx.params.get("invariant_id")
        if not invariant_id:
            return unavailable(_LEVEL, "invariant check invoked without an 'invariant_id' param")

        inv = next(
            (i for i in ctx.dossier.invariants if i.invariant_id == invariant_id),
            None,
        )
        if inv is None:
            return unavailable(
                _LEVEL,
                f"invariant '{invariant_id}' is not declared in the dossier",
                subject=str(invariant_id),
                subject_kind="workflow",
            )

        level = inv.level
        if not inv.check:
            return unavailable(
                level,
                (
                    f"invariant '{invariant_id}' declares no executable check; "
                    "an unenforceable hard constraint is a specification defect"
                ),
                subject=invariant_id,
                invariant_id=invariant_id,
                description=inv.description,
            )
        if inv.check == "invariant":
            return unavailable(
                level,
                f"invariant '{invariant_id}' dispatches to itself",
                subject=invariant_id,
                invariant_id=invariant_id,
            )
        if not registry.has(inv.check):
            return unavailable(
                level,
                (
                    f"invariant '{invariant_id}' names check '{inv.check}', "
                    "which is not registered"
                ),
                subject=invariant_id,
                invariant_id=invariant_id,
                dispatched_to=inv.check,
            )

        passthrough = {k: v for k, v in ctx.params.items() if k != "invariant_id"}
        inner_ctx = ctx.model_copy(update={"params": {**inv.params, **passthrough}})
        inner = registry.get(inv.check)(inner_ctx)
        return inner.model_copy(
            update={
                "level": level,
                "summary": f"invariant '{invariant_id}': {inner.summary}",
                "evidence": {
                    **inner.evidence,
                    "invariant_id": invariant_id,
                    "invariant_description": inv.description,
                    "dispatched_to": inv.check,
                },
            }
        )

    return invariant


def _artifacts_of_type(ctx: CheckContext, type_name: str) -> list[str]:
    return sorted(
        aid for aid, art in ctx.artifacts.items() if art.type_name == type_name
    )


def min_items(ctx: CheckContext) -> CheckResult:
    """Generic declarative invariant body: a payload collection has >= N items.

    Provided so that a dossier can express the most common hard constraint
    ("the result must not be trivially small") without shipping code. Params:
    ``artifact_type``, ``field`` (optional, for dict payloads), ``min``.
    """
    type_name: Optional[str] = ctx.params.get("artifact_type")
    minimum = ctx.params.get("min", 1)
    field = ctx.params.get("field")
    if not type_name:
        return unavailable(_LEVEL, "min_items requires an 'artifact_type' param")

    ids = _artifacts_of_type(ctx, type_name)
    if not ids:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"no artifact of type '{type_name}' was produced",
            score=0.0,
            subject=type_name,
            subject_kind="artifact",
            evidence={"artifact_type": type_name, "min": minimum},
        )

    counts: dict[str, int] = {}
    for aid in ids:
        payload = ctx.artifacts[aid].payload
        if field is not None:
            payload = payload.get(field) if isinstance(payload, dict) else None
        try:
            counts[aid] = len(payload)  # type: ignore[arg-type]
        except TypeError:
            counts[aid] = 0

    best = max(counts.values())
    if best < int(minimum):
        worst = sorted(counts)[0]
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"largest '{type_name}' payload has {best} item(s), below the minimum {minimum}",
            score=best / float(minimum) if minimum else 0.0,
            subject=worst,
            subject_kind="artifact",
            evidence={"counts": counts, "min": minimum, "field": field},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"'{type_name}' payload has {best} item(s), meeting the minimum {minimum}",
        score=1.0,
        evidence={"counts": counts, "min": minimum, "field": field},
    )


__all__ = [
    "exit_status",
    "schema_valid",
    "required_outputs_present",
    "no_empty_result",
    "make_invariant_check",
    "min_items",
]
