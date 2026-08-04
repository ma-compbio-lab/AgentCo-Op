"""The evaluation contract: which assertions this task requires, and whether
they could actually be evaluated.

The reviewer objection this module answers is "the workflow ran to completion"
being mistaken for "the result is good". Running to completion is an
observation about the *executor*; it says nothing about whether anybody was
able to check the output. So the contract is explicit about three separate
things that v1 collapsed into one boolean:

1. **What must be checked** — a list of :class:`CheckSpec`, derived from the
   dossier rather than invented per run.
2. **What could be checked** — :meth:`EvaluationContract.runnable_specs`
   splits the specs using ``dossier.availability(level)``. A task with no
   claim-level oracle does not silently lose its claim checks; they are
   emitted as :attr:`CheckStatus.UNAVAILABLE`.
3. **What the verdict was** — and ``UNAVAILABLE`` is never one of the passing
   verdicts, anywhere.

The doctrine every check implementation in this package follows:

    A check returns ``PASS`` only when it *affirmatively verified* the
    property. When the observations needed to decide are absent, it returns
    ``UNAVAILABLE`` and says which observation was missing. Vacuous truth is
    not verification.

That is stricter than it sounds. "No budget was declared" is not "the run was
within budget"; "no artifact declared its coverage" is not "conversion was
lossless". Both are reported as unmeasured, which is what lets
``utility_eval`` mark a whole utility dimension unavailable instead of
scoring it 0.0 or 1.0 and pretending it knew.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.dossier import EvaluatorAvailability, TaskEvidenceDossier
from agentcoop.ir.workflow import CompiledWorkflow

if TYPE_CHECKING:  # pragma: no cover - typing only
    # ``Trace`` is owned by ``agentcoop/execute/``. Importing it at runtime
    # would make the evaluation stack unusable until that subsystem lands, and
    # would create an import cycle once the execution engine starts emitting
    # checks. Checks therefore duck-type the trace against the interface in
    # the v2 contract (``events``, ``artifacts``, ``node_results``, ``lineage``).
    from agentcoop.execute.trace import Trace
else:  # pragma: no cover - runtime alias
    Trace = Any


SubjectKind = Literal["node", "edge", "artifact", "workflow", "claim", "component"]


# ---------------------------------------------------------------------------
# Specs and contexts
# ---------------------------------------------------------------------------


class CheckSpec(BaseModel):
    """One assertion the task requires, bound to a registered implementation.

    A spec is *declarative*: it names an implementation key rather than
    carrying a callable, so a contract can be serialized into a run manifest
    and re-evaluated later by a reviewer who was not present for the run.
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    level: CheckLevel
    #: Key into a :class:`~agentcoop.evaluate.registry.CheckRegistry`.
    impl: str
    params: dict[str, Any] = Field(default_factory=dict)
    #: Failing this check violates a hard requirement of the task.
    blocking: bool = False
    #: Node / edge / artifact / component this check is about, when known.
    subject: Optional[str] = None


class CheckContext(BaseModel):
    """Everything a check implementation is allowed to look at.

    Deliberately *not* given a component library, an adapter registry, or a
    network client: a check must be a pure, offline function of recorded
    evidence, otherwise re-evaluating a run manifest could produce a different
    verdict than the run did.
    """

    model_config = ConfigDict(extra="forbid")

    dossier: TaskEvidenceDossier
    workflow: Optional[CompiledWorkflow] = None
    #: All artifacts visible to the evaluation, keyed by artifact id. Checks
    #: read ``value.type_name`` rather than the key, so a caller that keys by
    #: type name instead still gets correct results.
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    trace: Optional[Trace] = None
    cost: CostProfile = Field(default_factory=CostProfile)
    params: dict[str, Any] = Field(default_factory=dict)
    subject: Optional[str] = None


#: A check implementation. Pure, deterministic, offline.
CheckFn = Callable[[CheckContext], CheckResult]

#: Builds the context for one spec. The indirection exists so that a caller
#: can vary ``subject``/``params`` per spec without materializing N contexts.
ContextFactory = Callable[[CheckSpec], CheckContext]


class CheckLookup(Protocol):
    """Structural type for the registry, so ``contract`` need not import it.

    ``CheckRegistry`` satisfies this. Keeping it structural avoids the cycle
    ``contract -> registry -> checks -> contract``.
    """

    def has(self, name: str) -> bool: ...

    def get(self, name: str) -> CheckFn: ...


# ---------------------------------------------------------------------------
# Result constructors
# ---------------------------------------------------------------------------


def make_result(
    level: CheckLevel,
    status: CheckStatus,
    summary: str,
    *,
    source: SignalSource = SignalSource.DETERMINISTIC,
    score: Optional[float] = None,
    subject: Optional[str] = None,
    subject_kind: SubjectKind = "workflow",
    evidence: Optional[dict[str, Any]] = None,
) -> CheckResult:
    """Build a result with an empty ``check_id``.

    Implementations do not know which spec invoked them —
    :meth:`EvaluationContract.evaluate` stamps identity onto the result. That
    keeps one implementation reusable across several specs without the
    implementation inventing ids that would collide in the report.
    """
    return CheckResult(
        check_id="",
        level=level,
        status=status,
        source=source,
        summary=summary,
        score=score,
        subject=subject,
        subject_kind=subject_kind,
        evidence=dict(evidence or {}),
    )


def unavailable(
    level: CheckLevel,
    reason: str,
    *,
    source: SignalSource = SignalSource.DETERMINISTIC,
    subject: Optional[str] = None,
    subject_kind: SubjectKind = "workflow",
    **evidence: Any,
) -> CheckResult:
    """The check could not be decided. Never a pass, never a score.

    ``score`` is left ``None`` on purpose: any numeric value here would be
    picked up by an averaging consumer and turned into a silent verdict.
    """
    return make_result(
        level,
        CheckStatus.UNAVAILABLE,
        reason,
        source=source,
        score=None,
        subject=subject,
        subject_kind=subject_kind,
        evidence={"missing_evidence": reason, **evidence},
    )


# ---------------------------------------------------------------------------
# Duck-typed accessors over the execution trace
# ---------------------------------------------------------------------------


def trace_node_results(trace: Any) -> dict[str, Any]:
    """``{node_id: InvocationResult}`` from a trace, or ``{}`` when absent."""
    if trace is None:
        return {}
    return dict(getattr(trace, "node_results", {}) or {})


def trace_events(trace: Any) -> list[Any]:
    if trace is None:
        return []
    return list(getattr(trace, "events", []) or [])


def trace_artifacts(trace: Any) -> dict[str, Artifact]:
    if trace is None:
        return {}
    return dict(getattr(trace, "artifacts", {}) or {})


def result_ok(result: Any) -> Optional[bool]:
    """Tri-state success of an ``InvocationResult``-shaped object.

    ``None`` means "the object does not report success", which is treated as
    *unknown* rather than as failure — inventing a failure would be as
    dishonest as inventing a pass.
    """
    value = getattr(result, "ok", None)
    return bool(value) if isinstance(value, bool) else None


def result_exit_code(result: Any) -> Optional[int]:
    code = getattr(result, "exit_code", None)
    return code if isinstance(code, int) else None


def result_errors(result: Any) -> list[str]:
    errors = getattr(result, "errors", None) or []
    return [str(e) for e in errors]


# ---------------------------------------------------------------------------
# Payload emptiness
# ---------------------------------------------------------------------------

_EMPTY_CONTAINERS = (str, bytes, list, tuple, set, frozenset, dict)


def payload_is_empty(payload: Any, *, fields: Optional[list[str]] = None) -> bool:
    """Whether an artifact payload carries no content.

    ``fields`` names the load-bearing keys of a dict payload, so a task can
    say "the gene list is what matters" and not have ``{"genes": [],
    "runtime_s": 3.2}`` count as non-empty just because a timing field is
    populated. That combination — a populated envelope around an empty result,
    with exit code 0 — is the silent failure this whole package exists to
    catch.

    A numeric zero is *not* empty: ``{"n_significant": 0}`` is a real finding.
    """
    if payload is None:
        return True
    if isinstance(payload, dict) and fields:
        present = [f for f in fields if f in payload]
        if not present:
            return True
        return all(payload_is_empty(payload[f]) for f in present)
    if isinstance(payload, dict):
        if not payload:
            return True
        return all(payload_is_empty(v) for v in payload.values())
    if isinstance(payload, _EMPTY_CONTAINERS):
        return len(payload) == 0
    return False


def content_fields(
    dossier: TaskEvidenceDossier,
    type_name: str,
    overrides: Optional[dict[str, Any]] = None,
) -> Optional[list[str]]:
    """Which payload keys carry the *content* of an artifact of this type.

    Falls back to the ``required`` properties of the type's structural
    schema, because that is precisely the declaration of what the artifact
    promises to contain. Without this fallback, ``{"genes": [],
    "runtime_s": 1.2}`` reads as non-empty — a populated timing field
    disguising a component that found nothing.
    """
    if overrides and type_name in overrides:
        value = overrides[type_name]
        if isinstance(value, (list, tuple)):
            return [str(f) for f in value]
    artifact_type = dossier.artifact_type(type_name)
    if artifact_type is None:
        return None
    required = artifact_type.json_schema.get("required")
    if isinstance(required, list) and required:
        return [str(f) for f in required]
    return None


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class EvaluationContract(BaseModel):
    """The set of checks this task requires, independent of any one run."""

    model_config = ConfigDict(extra="forbid")

    specs: list[CheckSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_check_ids(self) -> "EvaluationContract":
        seen: set[str] = set()
        for spec in self.specs:
            if spec.check_id in seen:
                raise ValueError(f"duplicate check_id '{spec.check_id}' in evaluation contract")
            seen.add(spec.check_id)
        return self

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dossier(cls, dossier: TaskEvidenceDossier) -> "EvaluationContract":
        """Derive the contract mechanically from the task specification.

        Every level of the stack is populated, including levels this task has
        no evaluator for. That is the point: a claim check that cannot run
        must appear in the report as unavailable, because "we never checked
        the claim" and "the claim checked out" have to be distinguishable in
        the manifest.
        """
        specs: list[CheckSpec] = []

        # -- HARD ------------------------------------------------------------
        specs.append(
            CheckSpec(
                check_id="hard:exit_status",
                level=CheckLevel.HARD,
                impl="exit_status",
                blocking=True,
            )
        )
        specs.append(
            CheckSpec(
                check_id="hard:required_outputs_present",
                level=CheckLevel.HARD,
                impl="required_outputs_present",
                params={"required": list(dossier.required_outputs)},
                blocking=True,
            )
        )
        specs.append(
            CheckSpec(
                check_id="hard:schema_valid",
                level=CheckLevel.HARD,
                impl="schema_valid",
                blocking=True,
            )
        )
        specs.append(
            CheckSpec(
                check_id="hard:no_empty_result",
                level=CheckLevel.HARD,
                impl="no_empty_result",
                params={"required": list(dossier.required_outputs)},
                blocking=True,
            )
        )
        for inv in dossier.invariants:
            # Dispatch goes through the ``invariant`` implementation rather
            # than inlining ``inv.check`` here, so that an invariant with no
            # executable check surfaces as an unavailable *blocking* check
            # instead of vanishing from the contract.
            specs.append(
                CheckSpec(
                    check_id=f"invariant:{inv.invariant_id}",
                    level=inv.level,
                    impl="invariant",
                    params={"invariant_id": inv.invariant_id},
                    blocking=True,
                )
            )

        # -- ARTIFACT --------------------------------------------------------
        for impl in (
            "facets_declared",
            "facet_match",
            "coverage_threshold",
            "provenance_complete",
            "no_silent_empty",
        ):
            specs.append(
                CheckSpec(check_id=f"artifact:{impl}", level=CheckLevel.ARTIFACT, impl=impl)
            )

        # -- PROCESS ---------------------------------------------------------
        specs.append(
            CheckSpec(
                check_id="process:required_steps_ran",
                level=CheckLevel.PROCESS,
                impl="required_steps_ran",
            )
        )
        specs.append(
            CheckSpec(
                check_id="process:verifier_present",
                level=CheckLevel.PROCESS,
                impl="verifier_present",
            )
        )
        specs.append(
            CheckSpec(
                check_id="process:sensitivity_ran",
                level=CheckLevel.PROCESS,
                impl="sensitivity_ran",
                params={
                    "required": sorted(
                        {
                            name
                            for req in dossier.evidence_chain
                            for name in req.required_sensitivity
                        }
                    )
                },
            )
        )
        specs.append(
            CheckSpec(
                check_id="process:negative_control_ran",
                level=CheckLevel.PROCESS,
                impl="negative_control_ran",
            )
        )

        # -- CLAIM -----------------------------------------------------------
        for impl in (
            "claim_bundle_wellformed",
            "claim_traceable",
            "claim_has_alternatives",
            "claim_uncertainty_declared",
        ):
            specs.append(CheckSpec(check_id=f"claim:{impl}", level=CheckLevel.CLAIM, impl=impl))

        # -- PREFERENCE ------------------------------------------------------
        specs.append(
            CheckSpec(
                check_id="preference:expert_pairwise",
                level=CheckLevel.PREFERENCE,
                impl="expert_pairwise",
            )
        )

        # -- RESOURCE --------------------------------------------------------
        for impl in ("within_budget", "within_wall_time", "reproducible"):
            specs.append(
                CheckSpec(check_id=f"resource:{impl}", level=CheckLevel.RESOURCE, impl=impl)
            )

        return cls(specs=specs)

    # -- introspection ------------------------------------------------------

    def spec(self, check_id: str) -> Optional[CheckSpec]:
        for s in self.specs:
            if s.check_id == check_id:
                return s
        return None

    def by_level(self, level: CheckLevel) -> list[CheckSpec]:
        return [s for s in self.specs if s.level == level]

    def with_specs(self, specs: list[CheckSpec]) -> "EvaluationContract":
        return EvaluationContract(specs=list(self.specs) + list(specs))

    # -- availability -------------------------------------------------------

    def runnable_specs(
        self, dossier: TaskEvidenceDossier
    ) -> tuple[list[CheckSpec], list[CheckSpec]]:
        """Split into ``(runnable, unavailable)`` by evaluator availability.

        A level the dossier declares ``UNAVAILABLE`` for has no oracle at all
        for this task, so its specs cannot run. Every other availability —
        including ``RUBRIC`` and ``PREFERENCE_ONLY`` — is runnable but is
        recorded on each result, because a rubric-derived pass and a
        deterministic pass are not the same evidence and must not be averaged
        as if they were.

        Order within each bucket follows ``self.specs`` so the split is
        deterministic.
        """
        runnable: list[CheckSpec] = []
        blocked: list[CheckSpec] = []
        for spec in self.specs:
            if dossier.availability(spec.level) is EvaluatorAvailability.UNAVAILABLE:
                blocked.append(spec)
            else:
                runnable.append(spec)
        return runnable, blocked

    # -- evaluation ---------------------------------------------------------

    def evaluate(
        self,
        ctx_factory: ContextFactory,
        registry: CheckLookup,
    ) -> CheckReport:
        """Evaluate every spec, emitting one result per spec — always.

        Four ways a spec can end up ``UNAVAILABLE``, and all four are recorded
        rather than skipped:

        * the task declares no evaluator for the level;
        * the named implementation is not registered;
        * the implementation raised (a broken evaluator is not a passing one);
        * the implementation itself reported it lacked the observations.
        """
        report = CheckReport()
        for spec in self.specs:
            report.add(self._evaluate_one(spec, ctx_factory, registry))
        return report

    def _evaluate_one(
        self,
        spec: CheckSpec,
        ctx_factory: ContextFactory,
        registry: CheckLookup,
    ) -> CheckResult:
        ctx = ctx_factory(spec)
        availability = ctx.dossier.availability(spec.level)

        if availability is EvaluatorAvailability.UNAVAILABLE:
            result = unavailable(
                spec.level,
                f"no evaluator available for level '{spec.level.value}' on this task",
                subject=spec.subject,
                evaluator_availability=availability.value,
                impl=spec.impl,
            )
        elif not registry.has(spec.impl):
            result = unavailable(
                spec.level,
                f"check implementation '{spec.impl}' is not registered",
                subject=spec.subject,
                evaluator_availability=availability.value,
                impl=spec.impl,
            )
        else:
            try:
                result = registry.get(spec.impl)(ctx)
            except Exception as exc:  # noqa: BLE001 - an evaluator crash is a coverage gap
                result = unavailable(
                    spec.level,
                    f"check implementation '{spec.impl}' raised {type(exc).__name__}",
                    subject=spec.subject,
                    evaluator_availability=availability.value,
                    impl=spec.impl,
                    error=f"{type(exc).__name__}: {exc}",
                )

        evidence = dict(result.evidence)
        evidence.setdefault("impl", spec.impl)
        evidence.setdefault("evaluator_availability", availability.value)
        return result.model_copy(
            update={
                "check_id": spec.check_id,
                "level": spec.level,
                "blocking": spec.blocking,
                "subject": result.subject or spec.subject,
                "evidence": evidence,
            }
        )


def make_context_factory(base: CheckContext) -> ContextFactory:
    """Adapt one base context into a per-spec factory.

    Spec params override base params, and a spec's subject wins over the base
    subject — a spec is the more specific statement of what is being checked.
    """

    def factory(spec: CheckSpec) -> CheckContext:
        return base.model_copy(
            update={
                "params": {**base.params, **spec.params},
                "subject": spec.subject or base.subject,
            }
        )

    return factory


__all__ = [
    "CheckSpec",
    "CheckContext",
    "CheckFn",
    "ContextFactory",
    "CheckLookup",
    "EvaluationContract",
    "SubjectKind",
    "make_result",
    "unavailable",
    "make_context_factory",
    "payload_is_empty",
    "content_fields",
    "trace_node_results",
    "trace_events",
    "trace_artifacts",
    "result_ok",
    "result_exit_code",
    "result_errors",
]
