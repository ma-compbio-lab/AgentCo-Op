"""Reading a certification back out: what was earned, and what is still missing.

``CapabilityCard.certification_level`` answers *what* a component earned. This
module answers *why*, and it does so by walking one shared description of the
ladder rather than a second, hand-written copy of the same conditions. A
divergence between the level and its explanation would be worse than having no
explanation at all — it would be an authoritative-looking wrong answer — so
:func:`derived_level` recomputes the level from the very steps the explanation
prints, and the test suite pins the two together.

The other commitment here is that a probe which never ran reports
``UNAVAILABLE``. It does not report ``PASS``, and it is not omitted from the
report. An absent negative probe is the difference between "this component
refuses bad input" and "nobody has ever asked it to", and a report that cannot
show the difference is not evidence.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from agentcoop.ir.capability import CapabilityCard, CertificationLevel
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.probe.spec import PROBE_KINDS

#: Which stratum of the evaluation contract each probe kind speaks to, and
#: whether failing it blocks composition.
KIND_LEVELS: dict[str, tuple[CheckLevel, bool]] = {
    "reachable": (CheckLevel.HARD, True),
    "schema": (CheckLevel.HARD, True),
    "smoke": (CheckLevel.ARTIFACT, True),
    "invalid_input": (CheckLevel.ARTIFACT, True),
    "determinism": (CheckLevel.PROCESS, False),
    "resource": (CheckLevel.RESOURCE, False),
}


class LadderStep(NamedTuple):
    """One rung: the level it grants, what it demands, and whether that holds."""

    level: CertificationLevel
    requirement: str
    met: bool
    blockers: list[str]


def ladder(card: CapabilityCard) -> list[LadderStep]:
    """The certification ladder, evaluated rung by rung.

    Order matters and is strict: a rung is only reached if every rung below it
    was met. The list is returned in full anyway, so a caller can see that a
    component passed its negative probes while still failing to be reachable —
    a combination that usually means the probes were run against a stale
    environment.
    """
    emp = card.empirical
    steps: list[LadderStep] = []

    has_contract = bool(card.io.produces or card.io.consumes)
    steps.append(
        LadderStep(
            CertificationLevel.DECLARED,
            "declares an input or output contract",
            has_contract,
            [] if has_contract else ["the card declares neither inputs nor outputs"],
        )
    )

    reachable_ok = bool(emp.probes) and emp.passed("reachable")
    reachable_blockers: list[str] = []
    if not emp.probes:
        reachable_blockers.append("no probe has ever been executed against this component")
    else:
        reachable_blockers.extend(_failures(card, "reachable"))
    steps.append(
        LadderStep(
            CertificationLevel.REACHABLE,
            "was executed and its entrypoint resolved",
            reachable_ok,
            reachable_blockers,
        )
    )

    probed_ok = emp.passed("schema") and emp.passed("smoke")
    steps.append(
        LadderStep(
            CertificationLevel.PROBED,
            "emits its declared outputs, structurally valid and non-vacuous",
            probed_ok,
            _failures(card, "schema") + _failures(card, "smoke"),
        )
    )

    certified_ok = emp.passed("invalid_input") and emp.passed("resource")
    steps.append(
        LadderStep(
            CertificationLevel.CERTIFIED,
            "refuses malformed input and honours a stated budget",
            certified_ok,
            _failures(card, "invalid_input") + _failures(card, "resource"),
        )
    )

    n = emp.reliability.n_observations
    lcb = emp.reliability.lcb
    trusted_ok = n >= 5 and lcb >= 0.6
    trusted_blockers: list[str] = []
    if n < 5:
        trusted_blockers.append(
            f"only {n:.0f} real-run observations recorded; 5 are required "
            "(probes do not count — reliability is earned in deployment)"
        )
    if lcb < 0.6:
        trusted_blockers.append(
            f"reliability lower bound {lcb:.2f} is below 0.60"
        )
    steps.append(
        LadderStep(
            CertificationLevel.TRUSTED,
            "has a reliability record over real runs of this task family",
            trusted_ok,
            trusted_blockers,
        )
    )

    return steps


def derived_level(card: CapabilityCard) -> CertificationLevel:
    """Recompute the level from :func:`ladder`.

    Exists so the explanation and the property cannot drift apart silently.
    """
    level = CertificationLevel.UNKNOWN
    for step in ladder(card):
        if not step.met:
            break
        level = step.level
    return level


def first_blocking_step(card: CapabilityCard) -> Optional[LadderStep]:
    """The rung that stopped the climb, or ``None`` when fully TRUSTED."""
    for step in ladder(card):
        if not step.met:
            return step
    return None


def _failures(card: CapabilityCard, kind: str) -> list[str]:
    """Human-readable blockers for one probe kind.

    A kind with no probes at all is reported as *unprobed* rather than as
    passing or failing, because the corrective action differs: run the probe
    versus fix the component.
    """
    outcomes = card.empirical.probes_of(kind)
    if not outcomes:
        return [f"the '{kind}' probe has never been run"]
    return [f"{kind}: {o.detail or 'failed'}" for o in outcomes if not o.passed]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def certification_report(card: CapabilityCard) -> CheckReport:
    """One check per probe kind, plus the earned level.

    Every kind appears, including the ones that were never run. That is the
    whole point: the gap between "passed" and "not attempted" has to be visible
    in the artifact a reviewer reads.
    """
    report = CheckReport()

    for kind in PROBE_KINDS:
        level, blocking = KIND_LEVELS[kind]
        outcomes = card.empirical.probes_of(kind)
        if not outcomes:
            report.add(
                CheckResult(
                    check_id=f"certification.{kind}",
                    level=level,
                    status=CheckStatus.UNAVAILABLE,
                    source=SignalSource.DETERMINISTIC,
                    summary=(
                        f"the '{kind}' probe was never executed against "
                        f"'{card.name}', so nothing is known either way"
                    ),
                    subject=card.name,
                    subject_kind="component",
                    blocking=blocking,
                    evidence={"probe_count": 0},
                )
            )
            continue

        failed = [o for o in outcomes if not o.passed]
        report.add(
            CheckResult(
                check_id=f"certification.{kind}",
                level=level,
                status=CheckStatus.PASS if not failed else CheckStatus.FAIL,
                source=SignalSource.DETERMINISTIC,
                summary=(
                    f"{len(outcomes)} '{kind}' probe(s) passed"
                    if not failed
                    else f"{len(failed)}/{len(outcomes)} '{kind}' probe(s) failed: "
                    + "; ".join(o.detail for o in failed[:3])
                ),
                score=(len(outcomes) - len(failed)) / len(outcomes),
                subject=card.name,
                subject_kind="component",
                blocking=blocking,
                evidence={
                    "probe_ids": [o.probe_id for o in outcomes],
                    "failed": [o.probe_id for o in failed],
                },
            )
        )

    for outcome in card.empirical.probes_of("invalid_input"):
        if outcome.passed or not outcome.evidence.get("silent"):
            continue
        report.add(
            CheckResult(
                check_id=f"certification.silent_failure.{outcome.probe_id}",
                level=CheckLevel.ARTIFACT,
                status=CheckStatus.FAIL,
                source=SignalSource.DETERMINISTIC,
                summary=(
                    f"'{card.name}' does not fail loudly: {outcome.detail}. Any "
                    "downstream node would treat this output as a real result."
                ),
                subject=card.name,
                subject_kind="component",
                blocking=True,
                evidence={
                    "silent_mode": outcome.evidence.get("silent_mode"),
                    "defect": outcome.evidence.get("defect"),
                },
            )
        )

    level = card.certification_level
    blocker = first_blocking_step(card)
    report.add(
        CheckResult(
            check_id="certification.level",
            level=CheckLevel.PROCESS,
            status=CheckStatus.PASS
            if level >= CertificationLevel.PROBED
            else CheckStatus.WARN
            if level >= CertificationLevel.REACHABLE
            else CheckStatus.FAIL,
            source=SignalSource.DETERMINISTIC,
            summary=explain_level(card),
            score=level / CertificationLevel.TRUSTED,
            subject=card.name,
            subject_kind="component",
            # A component that has not demonstrated it emits its declared
            # outputs is a hard-constraint failure, not a soft warning. Without
            # this the report would satisfy `hard_constraints_satisfied` purely
            # because the probes that would have failed were never run.
            blocking=level < CertificationLevel.PROBED,
            evidence={
                "level": level.name,
                "next_level": blocker.level.name if blocker else None,
                "blockers": list(blocker.blockers) if blocker else [],
            },
        )
    )
    return report


def explain_level(card: CapabilityCard) -> str:
    """Why this component sits where it does, and what would move it up."""
    level = card.certification_level
    lines = [f"{card.name}: {level.name}"]

    for step in ladder(card):
        mark = "ok  " if step.met else "no  "
        lines.append(f"  [{mark}] {step.level.name:<9} {step.requirement}")
        if not step.met:
            for blocker in step.blockers or ["requirement not met"]:
                lines.append(f"              - {blocker}")

    blocker = first_blocking_step(card)
    if blocker is None:
        lines.append("  every rung is satisfied; this component is fully trusted")
    else:
        lines.append(
            f"  to reach {blocker.level.name}: {blocker.requirement}"
        )
        if level <= CertificationLevel.DECLARED:
            lines.append(
                "  nothing here has been demonstrated by execution; the compiler "
                "will refuse to bind this component to any subgoal that requires "
                "more than a declaration"
            )
    return "\n".join(lines)


__all__ = [
    "LadderStep",
    "ladder",
    "derived_level",
    "first_blocking_step",
    "certification_report",
    "explain_level",
    "KIND_LEVELS",
]
