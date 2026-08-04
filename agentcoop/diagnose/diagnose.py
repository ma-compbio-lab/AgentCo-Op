"""Ranked root-cause hypotheses.

The output is deliberately a *distribution*, not a conclusion. v1 produced a
single verdict and acted on it; here the diagnoser emits a normalized set over
the fault taxonomy, retains the signals that argue against each hypothesis, and
exposes an entropy so the repair loop can tell the difference between "I know
what is wrong" and "several things could explain this".

Likelihoods are rule-based and explainable. Priors come from recorded failure
distributions when a statistics store is supplied. An optional LLM hook may
reorder within the hypothesis set and add prose, but it cannot introduce a
hypothesis no signal supports — that constraint is enforced in code, not by
instruction.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict

from agentcoop.diagnose.localize import Localization
from agentcoop.diagnose.signals import SignalKind, SignalSet
from agentcoop.ir.capability import ComponentLibrary
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.faults import BlameTarget, Diagnosis, FaultClass, FaultHypothesis
from agentcoop.ir.workflow import CompiledWorkflow, atomics

#: (signal kind) -> {fault class: likelihood contribution}.
#:
#: Every row is a claim a reviewer can argue with, which is the point. Note
#: that no row maps a symptom onto exactly one cause: an empty output is
#: usually a capability or contract problem, but a broken tool produces one
#: too, and sometimes the honest answer is that the data say nothing.
LIKELIHOOD: dict[SignalKind, dict[FaultClass, float]] = {
    SignalKind.SCHEMA_VIOLATION: {
        FaultClass.ARTIFACT_CONTRACT: 0.60,
        FaultClass.CONFIGURATION: 0.20,
        FaultClass.CAPABILITY_MISMATCH: 0.20,
    },
    SignalKind.FACET_VIOLATION: {
        FaultClass.ARTIFACT_CONTRACT: 0.75,
        FaultClass.CAPABILITY_MISMATCH: 0.15,
        FaultClass.TASK_SPECIFICATION: 0.10,
    },
    SignalKind.HANDOFF_REJECTED: {
        FaultClass.ARTIFACT_CONTRACT: 0.70,
        FaultClass.COORDINATION: 0.20,
        FaultClass.CAPABILITY_MISMATCH: 0.10,
    },
    SignalKind.TOOL_ERROR: {
        FaultClass.TOOL_FAILURE: 0.55,
        FaultClass.ENVIRONMENT: 0.25,
        FaultClass.CONFIGURATION: 0.20,
    },
    SignalKind.ADAPTER_MISSING: {
        FaultClass.ENVIRONMENT: 0.85,
        FaultClass.TASK_SPECIFICATION: 0.15,
    },
    SignalKind.TIMEOUT: {
        FaultClass.ENVIRONMENT: 0.40,
        FaultClass.TOOL_FAILURE: 0.35,
        FaultClass.CONFIGURATION: 0.25,
    },
    SignalKind.EMPTY_OUTPUT: {
        FaultClass.CAPABILITY_MISMATCH: 0.35,
        FaultClass.ARTIFACT_CONTRACT: 0.35,
        FaultClass.CONFIGURATION: 0.20,
        FaultClass.IRREDUCIBLE_UNCERTAINTY: 0.10,
    },
    SignalKind.MERGE_REFUSED: {
        FaultClass.COORDINATION: 0.55,
        FaultClass.ARTIFACT_CONTRACT: 0.45,
    },
    # An ordering that carries no data is a property of the graph, not of any
    # component in it, so the mass sits almost entirely on coordination. The
    # residue is task specification: the spurious dependency may have been
    # declared in the dossier rather than invented by the compiler.
    SignalKind.NEEDLESS_SERIALIZATION: {
        FaultClass.COORDINATION: 0.75,
        FaultClass.TASK_SPECIFICATION: 0.25,
    },
    SignalKind.BRANCH_DISAGREEMENT: {
        FaultClass.COORDINATION: 0.35,
        FaultClass.CAPABILITY_MISMATCH: 0.25,
        FaultClass.IRREDUCIBLE_UNCERTAINTY: 0.25,
        FaultClass.CONFIGURATION: 0.15,
    },
    SignalKind.MISSING_EVIDENCE: {
        FaultClass.COORDINATION: 0.50,
        FaultClass.TASK_SPECIFICATION: 0.30,
        FaultClass.EVALUATOR_FAILURE: 0.20,
    },
    SignalKind.COST_ANOMALY: {
        FaultClass.CONFIGURATION: 0.50,
        FaultClass.ENVIRONMENT: 0.30,
        FaultClass.COORDINATION: 0.20,
    },
    SignalKind.PROGRESS_STALL: {
        FaultClass.COORDINATION: 0.50,
        FaultClass.TOOL_FAILURE: 0.30,
        FaultClass.ENVIRONMENT: 0.20,
    },
    SignalKind.EVALUATOR_UNAVAILABLE: {
        FaultClass.EVALUATOR_FAILURE: 0.60,
        FaultClass.TASK_SPECIFICATION: 0.40,
    },
    SignalKind.CHECK_FAILURE: {
        FaultClass.CONFIGURATION: 0.30,
        FaultClass.CAPABILITY_MISMATCH: 0.30,
        FaultClass.ARTIFACT_CONTRACT: 0.20,
        FaultClass.EVALUATOR_FAILURE: 0.20,
    },
}

#: Localization re-weights the estimate. Blame landing on an *artifact* makes a
#: contract fault far more likely than a configuration one — this is where the
#: structural finding from backward slicing enters the numbers, and it is what
#: stops "the test failed" defaulting to "retry the node".
BLAME_WEIGHT: dict[BlameTarget, dict[FaultClass, float]] = {
    BlameTarget.ARTIFACT: {
        FaultClass.ARTIFACT_CONTRACT: 2.0,
        FaultClass.CAPABILITY_MISMATCH: 1.3,
        FaultClass.CONFIGURATION: 0.6,
        FaultClass.ENVIRONMENT: 0.5,
    },
    BlameTarget.EDGE: {
        FaultClass.ARTIFACT_CONTRACT: 1.8,
        FaultClass.COORDINATION: 1.4,
        FaultClass.CONFIGURATION: 0.6,
    },
    BlameTarget.NODE: {
        FaultClass.CONFIGURATION: 1.4,
        FaultClass.TOOL_FAILURE: 1.3,
        FaultClass.CAPABILITY_MISMATCH: 1.2,
        FaultClass.ARTIFACT_CONTRACT: 0.7,
    },
    BlameTarget.TOPOLOGY: {
        FaultClass.COORDINATION: 2.0,
        FaultClass.TASK_SPECIFICATION: 1.2,
        FaultClass.CONFIGURATION: 0.5,
    },
    BlameTarget.ENVIRONMENT: {FaultClass.ENVIRONMENT: 2.0},
    BlameTarget.EVALUATOR: {FaultClass.EVALUATOR_FAILURE: 2.0},
}

#: An LLM refinement hook. Receives the diagnosis and returns a reordering of
#: the hypotheses it was given. Anything it invents is discarded.
LLMRefiner = Callable[[Diagnosis, dict[str, Any]], Diagnosis]


class DiagnosisOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis: Diagnosis
    localization: Localization
    #: True when the hypothesis set is too diffuse to act on.
    needs_more_evidence: bool = False
    evidence_gathering_hint: str = ""


class Diagnoser:
    def __init__(
        self,
        *,
        statistics: Any = None,
        llm: Optional[LLMRefiner] = None,
        entropy_threshold: float = 1.5,
    ) -> None:
        self.statistics = statistics
        self.llm = llm
        self.entropy_threshold = entropy_threshold

    def diagnose(
        self,
        signals: SignalSet,
        localization: Localization,
        workflow: Optional[CompiledWorkflow] = None,
        dossier: Optional[TaskEvidenceDossier] = None,
        library: Optional[ComponentLibrary] = None,
    ) -> DiagnosisOutcome:
        scores: dict[FaultClass, float] = {}
        supporting: dict[FaultClass, list[str]] = {}
        contradicting: dict[FaultClass, list[str]] = {}

        blocking = signals.blocking() or list(signals.signals)
        for signal in blocking:
            row = LIKELIHOOD.get(signal.kind, {})
            for fault, weight in row.items():
                scores[fault] = scores.get(fault, 0.0) + weight
                supporting.setdefault(fault, []).append(signal.signal_id)

        # Localization is itself an evidence source, and a decisive one. The
        # backward slice can establish that an upstream artifact violates its
        # own contract even when no detector fired on it -- the detectors see
        # the *downstream* symptom. Without this, a run where a bad artifact
        # made a later component crash would be diagnosed as a tool failure,
        # which is exactly the misattribution this subsystem exists to remove.
        localization_row, localization_ref = _localization_contribution(localization)
        for fault, weight in localization_row.items():
            scores[fault] = scores.get(fault, 0.0) + weight
            supporting.setdefault(fault, []).append(localization_ref)

        # A signal whose likelihood row excludes a fault is evidence against
        # it. Recording that is what makes the diagnosis auditable rather than
        # merely plausible.
        for signal in blocking:
            row = LIKELIHOOD.get(signal.kind, {})
            for fault in scores:
                if fault not in row:
                    contradicting.setdefault(fault, []).append(signal.signal_id)

        if not scores:
            return DiagnosisOutcome(
                diagnosis=Diagnosis(
                    localized_to=localization.subject,
                    localized_kind=localization.subject_kind,
                    notes=["no signal carried diagnostic weight"],
                ),
                localization=localization,
            )

        for fault, multiplier in BLAME_WEIGHT.get(localization.subject_kind, {}).items():
            if fault in scores:
                scores[fault] *= multiplier

        scores = self._apply_priors(scores, localization, workflow)

        total = sum(scores.values())
        hypotheses = [
            FaultHypothesis(
                fault_class=fault,
                probability=score / total,
                blame_target=localization.subject_kind,
                subject=localization.subject,
                rationale=_rationale(fault, localization),
                supporting_signals=sorted(set(supporting.get(fault, []))),
                contradicting_signals=sorted(set(contradicting.get(fault, []))),
            )
            for fault, score in sorted(scores.items(), key=lambda kv: kv[0].value)
            if score > 0
        ]
        diagnosis = Diagnosis(
            hypotheses=hypotheses,
            localized_to=localization.subject,
            localized_kind=localization.subject_kind,
            trigger_signals=[s.signal_id for s in blocking],
            notes=[localization.rationale] if localization.rationale else [],
        ).normalized()

        if self.llm is not None:
            diagnosis = self._refine(diagnosis)

        entropy = diagnosis.entropy
        needs_more = entropy > self.entropy_threshold
        return DiagnosisOutcome(
            diagnosis=diagnosis,
            localization=localization,
            needs_more_evidence=needs_more,
            evidence_gathering_hint=(
                f"hypothesis entropy {entropy:.2f} bits exceeds the "
                f"{self.entropy_threshold} bit threshold; re-run the implicated "
                "subgraph with additional instrumentation rather than patching on a guess"
                if needs_more
                else ""
            ),
        )

    # -- helpers ------------------------------------------------------------

    def _apply_priors(
        self,
        scores: dict[FaultClass, float],
        localization: Localization,
        workflow: Optional[CompiledWorkflow],
    ) -> dict[FaultClass, float]:
        if self.statistics is None or workflow is None:
            return scores
        getter = getattr(self.statistics, "failure_distribution", None)
        if not callable(getter):
            return scores

        component = _component_at(workflow, localization.subject)
        if component is None:
            return scores
        distribution = getter(component) or {}
        if not distribution:
            return scores

        adjusted = dict(scores)
        for fault, prior in distribution.items():
            try:
                key = fault if isinstance(fault, FaultClass) else FaultClass(str(fault))
            except ValueError:
                continue
            if key in adjusted:
                # Multiplicative and centred on 1, so a component with no
                # distinctive history changes nothing.
                adjusted[key] *= 1.0 + float(prior)
        return adjusted

    def _refine(self, diagnosis: Diagnosis) -> Diagnosis:
        """Let a model reorder, never invent.

        Any hypothesis the refiner returns that no signal supported is dropped,
        and probabilities come from the rule-based estimate rather than from
        the model.
        """
        assert self.llm is not None
        allowed = {h.fault_class: h for h in diagnosis.hypotheses}
        try:
            refined = self.llm(diagnosis, {})
        except Exception:
            return diagnosis
        kept = [allowed[h.fault_class] for h in refined.hypotheses if h.fault_class in allowed]
        if not kept:
            return diagnosis
        seen = {k.fault_class for k in kept}
        remainder = [h for f, h in allowed.items() if f not in seen]
        return diagnosis.model_copy(
            update={
                "hypotheses": kept + remainder,
                "notes": diagnosis.notes
                + ["llm refinement applied: reordering only, no new hypotheses"],
            }
        )


def _localization_contribution(
    localization: Localization,
) -> tuple[dict[FaultClass, float], str]:
    """Likelihood mass implied by the backward slice's own finding.

    Weighted above any single symptom signal, because "this artifact violates
    the contract it declared" is a structural fact about the run, not an
    inference from an error message.
    """
    if (
        localization.subject_kind is not BlameTarget.ARTIFACT
        or not localization.earliest_anomalous_artifact
    ):
        return {}, ""

    problems: list[str] = []
    for verdict in localization.verdicts:
        if verdict.artifact_id == localization.earliest_anomalous_artifact:
            problems = verdict.problems
            break
    if not problems:
        return {}, ""

    row: dict[FaultClass, float] = {}
    for problem in problems:
        lowered = problem.lower()
        if "facet" in lowered:
            row[FaultClass.ARTIFACT_CONTRACT] = row.get(FaultClass.ARTIFACT_CONTRACT, 0.0) + 1.2
        elif "empty" in lowered:
            # An empty-but-well-formed artifact is as often "the component
            # cannot do this" as "the contract is wrong", and occasionally the
            # data genuinely support nothing.
            row[FaultClass.CAPABILITY_MISMATCH] = row.get(FaultClass.CAPABILITY_MISMATCH, 0.0) + 0.6
            row[FaultClass.ARTIFACT_CONTRACT] = row.get(FaultClass.ARTIFACT_CONTRACT, 0.0) + 0.4
            row[FaultClass.IRREDUCIBLE_UNCERTAINTY] = (
                row.get(FaultClass.IRREDUCIBLE_UNCERTAINTY, 0.0) + 0.2
            )
        else:  # structural schema violation
            row[FaultClass.ARTIFACT_CONTRACT] = row.get(FaultClass.ARTIFACT_CONTRACT, 0.0) + 0.9
    return row, f"localization:{localization.earliest_anomalous_artifact}"


def _component_at(workflow: CompiledWorkflow, subject: Optional[str]) -> Optional[str]:
    if not subject:
        return None
    for atom in atomics(workflow.term):
        if subject == atom.term_id or subject.startswith(atom.term_id):
            return atom.component
    return None


def _rationale(fault: FaultClass, localization: Localization) -> str:
    where = (
        f"{localization.subject_kind.value} '{localization.subject}'"
        if localization.subject
        else "the workflow"
    )
    return f"{fault.value} is consistent with the signals observed at {where}"


__all__ = ["Diagnoser", "DiagnosisOutcome", "LIKELIHOOD", "BLAME_WEIGHT", "LLMRefiner"]
