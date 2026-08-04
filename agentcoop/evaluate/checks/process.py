"""PROCESS-level checks: was the analysis actually *done*, not just finished?

A result can be structurally perfect and scientifically worthless because the
sensitivity analysis was skipped, the negative control never ran, or the step
that was supposed to independently verify the output was quietly dropped
during repair. None of that shows up at the HARD or ARTIFACT levels, because
nothing crashed and nothing violated a schema.

These checks read the recorded trace and the compiled workflow. They never
infer that a step ran from the fact that a later step produced output.
"""

from __future__ import annotations

from typing import Any, Iterable

from agentcoop.evaluate.contract import (
    CheckContext,
    make_result,
    result_ok,
    trace_events,
    trace_node_results,
    unavailable,
)
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus, SignalSource
from agentcoop.ir.workflow import Verify, atomics, iter_terms

_LEVEL = CheckLevel.PROCESS


def _successful_nodes(ctx: CheckContext) -> set[str]:
    """Node ids whose recorded result reported success.

    A node with no recorded ``ok`` flag is *not* counted as successful: the
    absence of a failure record is not a success record.
    """
    results = trace_node_results(ctx.trace)
    return {node_id for node_id, res in results.items() if result_ok(res) is True}


def required_steps_ran(ctx: CheckContext) -> CheckResult:
    """Every non-optional subgoal was served by a node that ran and succeeded.

    Two distinct failures are separated in the evidence because they demand
    different repairs: a subgoal with *no node at all* is a coordination /
    compilation fault, while a subgoal whose node never completed is an
    execution fault.
    """
    required = sorted(
        s.subgoal_id
        for s in ctx.dossier.subgoals
        if not s.optional
        and s.subgoal_id not in set(ctx.params.get("exclude_subgoals") or [])
    )
    if not required:
        return unavailable(
            _LEVEL,
            "the dossier declares no mandatory subgoals; there is no required step to verify",
        )
    if ctx.workflow is None:
        return unavailable(_LEVEL, "no compiled workflow; cannot map nodes to subgoals")
    if not trace_node_results(ctx.trace):
        return unavailable(_LEVEL, "no node results recorded; step execution is unobserved")

    graph = ctx.workflow.graph()
    nodes_by_subgoal: dict[str, list[str]] = {}
    for node in graph.nodes:
        if node.subgoal_id:
            nodes_by_subgoal.setdefault(node.subgoal_id, []).append(node.node_id)

    succeeded = _successful_nodes(ctx)
    unbound: list[str] = []
    not_run: list[str] = []
    for subgoal_id in required:
        candidates = nodes_by_subgoal.get(subgoal_id, [])
        if not candidates:
            unbound.append(subgoal_id)
        elif not any(node_id in succeeded for node_id in candidates):
            not_run.append(subgoal_id)

    broken = sorted(set(unbound) | set(not_run))
    if broken:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(broken)} of {len(required)} required subgoal(s) were not "
                f"completed: {', '.join(broken)}"
            ),
            score=1.0 - len(broken) / len(required),
            subject=broken[0],
            subject_kind="node",
            evidence={
                "subgoals_without_a_node": sorted(unbound),
                "subgoals_never_completed": sorted(not_run),
                "required": required,
            },
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(required)} required subgoal(s) completed successfully",
        score=1.0,
        evidence={"required": required},
    )


def verifier_present(ctx: CheckContext) -> CheckResult:
    """Subgoals flagged ``requires_verification`` sit under a ``Verify`` term.

    Vacuously satisfied when no subgoal requests verification: unlike a
    missing observation, that is an explicit per-subgoal decision recorded in
    the dossier, so the requirement set is genuinely known and genuinely
    empty. The summary says so rather than implying an independent check ran.
    """
    required = sorted(s.subgoal_id for s in ctx.dossier.subgoals if s.requires_verification)
    if not required:
        return make_result(
            _LEVEL,
            CheckStatus.PASS,
            "no subgoal declares requires_verification; nothing to verify",
            score=1.0,
            evidence={"required": [], "vacuous": True},
        )
    if ctx.workflow is None:
        return unavailable(
            _LEVEL,
            "no compiled workflow; verifier coverage cannot be determined",
            required=required,
        )

    verified: set[str] = set()
    for term in iter_terms(ctx.workflow.term):
        if isinstance(term, Verify):
            verified.update(a.subgoal_id for a in atomics(term.body))

    missing = sorted(set(required) - verified)
    if missing:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(missing)} subgoal(s) require independent verification but "
                f"have no verifier: {', '.join(missing)}"
            ),
            score=1.0 - len(missing) / len(required),
            subject=missing[0],
            subject_kind="node",
            evidence={"required": required, "unverified": missing, "verified": sorted(verified)},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(required)} subgoal(s) requiring verification are wrapped in a verifier",
        score=1.0,
        evidence={"required": required, "verified": sorted(verified)},
    )


def _event_names(ctx: CheckContext, kinds: Iterable[str]) -> set[str]:
    """Names carried by trace events of the given kinds."""
    wanted = set(kinds)
    names: set[str] = set()
    for event in trace_events(ctx.trace):
        if getattr(event, "kind", None) not in wanted:
            continue
        payload: Any = getattr(event, "payload", None)
        if isinstance(payload, dict):
            for key in ("name", "analysis", "control", "id"):
                value = payload.get(key)
                if isinstance(value, str):
                    names.add(value)
    return names


def _node_config_names(ctx: CheckContext, key: str) -> set[str]:
    """Values of ``config[key]`` on workflow nodes, as a set of names.

    Accepts a string, a list of strings, or ``True`` (in which case the node
    id itself is the name).
    """
    if ctx.workflow is None:
        return set()
    names: set[str] = set()
    for node in ctx.workflow.graph().nodes:
        value = node.config.get(key)
        if value is True:
            names.add(node.node_id)
            if node.subgoal_id:
                names.add(node.subgoal_id)
        elif isinstance(value, str):
            names.add(value)
        elif isinstance(value, (list, tuple)):
            names.update(str(v) for v in value)
    return names


def _observation_source(ctx: CheckContext) -> SignalSource:
    """Where a process observation came from.

    Kept explicit in the report because the provenance matters: a sensitivity
    result read off a recorded trace is an observation of repeated execution,
    whereas one supplied through ``params`` is a deterministic assertion about
    the manifest, and the two do not warrant the same trust.
    """
    if trace_events(ctx.trace) or trace_node_results(ctx.trace):
        return SignalSource.STATISTICAL
    return SignalSource.DETERMINISTIC


def sensitivity_ran(ctx: CheckContext) -> CheckResult:
    """The sensitivity analyses the evidence chain demands were executed.

    Requirements come from ``EvidenceChainRequirement.required_sensitivity``.
    When the dossier demands none, robustness is *unmeasured* rather than
    perfect — reported as unavailable so ``utility_eval`` marks the robustness
    dimension unavailable instead of scoring it.
    """
    required = sorted(
        {str(x) for x in (ctx.params.get("required") or [])}
        or {
            name
            for req in ctx.dossier.evidence_chain
            for name in req.required_sensitivity
        }
    )
    if not required:
        return unavailable(
            _LEVEL,
            (
                "the evidence chain declares no required sensitivity analysis; "
                "robustness of the result is unmeasured"
            ),
        )
    if ctx.trace is None and ctx.workflow is None and "ran" not in ctx.params:
        return unavailable(
            _LEVEL,
            "no trace or workflow recorded; sensitivity execution is unobserved",
            required=required,
        )

    ran: set[str] = {str(x) for x in (ctx.params.get("ran") or [])}
    ran |= _event_names(ctx, ("sensitivity", "sensitivity_analysis"))
    ran |= _node_config_names(ctx, "sensitivity")
    succeeded = _successful_nodes(ctx)
    ran |= {node_id for node_id in succeeded if node_id in set(required)}
    if ctx.workflow is not None:
        for node in ctx.workflow.graph().nodes:
            if node.subgoal_id in set(required) and node.node_id in succeeded:
                ran.add(node.subgoal_id)

    missing = sorted(set(required) - ran)
    if missing:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"{len(missing)} of {len(required)} required sensitivity "
                f"analysis(es) did not run: {', '.join(missing)}"
            ),
            source=_observation_source(ctx),
            score=1.0 - len(missing) / len(required),
            evidence={"required": required, "missing": missing, "ran": sorted(ran)},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(required)} required sensitivity analysis(es) ran",
        source=_observation_source(ctx),
        score=1.0,
        evidence={"required": required, "ran": sorted(ran)},
    )


def negative_control_ran(ctx: CheckContext) -> CheckResult:
    """A declared negative control was executed and completed.

    Without a negative control there is no evidence separating signal from
    pipeline artifact, so an undeclared control is reported as unavailable —
    not as a pass, and not as a failure of the run, because the omission is a
    property of the design rather than of the execution.
    """
    declared: set[str] = {str(x) for x in (ctx.params.get("controls") or [])}
    declared |= _node_config_names(ctx, "negative_control")
    marker = str(ctx.params.get("control_note", "negative_control"))
    for subgoal in ctx.dossier.subgoals:
        if any(marker in note for note in subgoal.notes):
            declared.add(subgoal.subgoal_id)

    if not declared:
        return unavailable(
            _LEVEL,
            (
                "no negative control is declared anywhere in the dossier or the "
                "workflow; signal cannot be distinguished from pipeline artifact"
            ),
        )
    if not trace_node_results(ctx.trace):
        return unavailable(
            _LEVEL,
            "no node results recorded; negative control execution is unobserved",
            declared_controls=sorted(declared),
        )

    succeeded = _successful_nodes(ctx)
    node_subgoal = {}
    if ctx.workflow is not None:
        node_subgoal = {
            n.node_id: n.subgoal_id for n in ctx.workflow.graph().nodes if n.subgoal_id
        }
    completed = set(succeeded) | {node_subgoal[n] for n in succeeded if n in node_subgoal}

    missing = sorted(declared - completed)
    if missing:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            f"declared negative control(s) did not complete: {', '.join(missing)}",
            score=1.0 - len(missing) / len(declared),
            subject=missing[0],
            subject_kind="node",
            evidence={"declared_controls": sorted(declared), "missing": missing},
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        f"all {len(declared)} declared negative control(s) completed",
        score=1.0,
        evidence={"declared_controls": sorted(declared)},
    )


__all__ = [
    "required_steps_ran",
    "verifier_present",
    "sensitivity_ran",
    "negative_control_ran",
]
