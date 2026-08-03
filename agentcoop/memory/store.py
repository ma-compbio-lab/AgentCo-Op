"""Append-only outcome memory.

This module records *what actually happened* so that later compilations can
cite evidence instead of guessing. Everything here exists to make one
distinction survivable in code:

    "the workflow ran to completion"  !=  "the result is good"

Three rules follow from that, and they are enforced by validators rather than
by convention, because a convention is exactly what gets forgotten when a run
record is assembled at 2am:

1. **An outcome has three states, not two.** :class:`Outcome` adds
   ``UNDETERMINED`` alongside success and failure. When no evaluator could
   reach a verdict we record that we do not know. An unavailable evaluator is
   not a passing evaluator, so ``UNDETERMINED`` observations update *neither*
   side of a Beta posterior (see :mod:`agentcoop.memory.statistics`).
2. **A component's self-report is not a verdict.** ``self_reported_ok`` is
   stored separately from ``outcome``. Their disagreement — exit code 0 with
   an empty or invalid artifact — is the silent-failure case, and it counts as
   a failure in the statistics, never as a success.
3. **An unproven compatibility is not a compatibility.** An edge whose facets
   were ``UNDERSPECIFIED`` cannot be recorded as a successful handoff, so no
   amount of "it seemed to work" can accumulate into evidence that the edge is
   safe.

Storage is append-only JSONL. Records are never rewritten, so a run's history
is auditable, and the serialization is canonical (sorted keys, sorted sets) so
that two identical histories produce byte-identical files.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentcoop.ir.artifacts import Compatibility
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import CheckLevel, CheckReport, CheckResult, CheckStatus
from agentcoop.ir.faults import FAULT_TAXONOMY, Diagnosis, FaultClass, RepairTier
from agentcoop.ir.utility import UtilityVector


class Outcome(str, Enum):
    """The verdict on one observation.

    ``UNDETERMINED`` is the load-bearing member. Collapsing it into either
    ``SUCCESS`` (optimistic) or ``FAILURE`` (pessimistic) would fabricate
    evidence: the first inflates reliability for components nobody could
    evaluate, the second punishes them for the evaluator's absence.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    UNDETERMINED = "undetermined"


class RunStatus(str, Enum):
    """How the run *terminated*. Deliberately says nothing about quality.

    ``COMPLETED`` means the engine reached the end of the graph. Whether the
    result was any good is answered by the checks, by the utility vector, and
    by :attr:`RunRecord.hard_validity` — never by this field.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    #: Terminated early by a budget or resource limit.
    ABORTED = "aborted"
    #: Suspended awaiting human review.
    ESCALATED = "escalated"


# ---------------------------------------------------------------------------
# Per-observation records
# ---------------------------------------------------------------------------


class ComponentOutcome(BaseModel):
    """One component invocation, judged.

    Keyed by component *name* rather than node id: reliability is a property
    of the component, and the same component may appear at several nodes.
    ``node_id`` is retained so a posterior can be traced back to the exact
    invocation that moved it.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    component: str
    subgoal_id: Optional[str] = None
    outcome: Outcome
    #: What the adapter itself reported (exit code, ``InvocationResult.ok``).
    #: Kept apart from ``outcome`` so silent failures are visible.
    self_reported_ok: bool = True
    #: Diagnosed root cause, when the run was diagnosed. Only meaningful on a
    #: failure: an observation is not a diagnosis, so this is filled in by
    #: whoever ran diagnosis, not inferred here.
    fault_class: Optional[FaultClass] = None
    #: True once a repair admissible for ``fault_class`` demonstrably fixed
    #: the failure (or ground truth was injected by the benchmark). Unconfirmed
    #: classes are still counted, but the distinction is preserved so a wrong
    #: diagnosis cannot quietly harden into a prior.
    fault_class_confirmed: bool = False
    #: Check ids consulted to reach ``outcome``. Makes the verdict re-derivable.
    checks_consulted: list[str] = Field(default_factory=list)
    detail: str = ""

    @property
    def silent(self) -> bool:
        """The component claimed success and was wrong.

        The most dangerous failure mode in a heterogeneous pipeline: nothing
        raises, the exit code is 0, and a plausible-looking empty artifact
        flows downstream.
        """
        return self.self_reported_ok and self.outcome is Outcome.FAILURE

    @model_validator(mode="after")
    def _fault_class_only_on_failure(self) -> "ComponentOutcome":
        if self.fault_class is not None and self.outcome is not Outcome.FAILURE:
            raise ValueError(
                f"component outcome for '{self.component}' is {self.outcome.value} but "
                "carries a fault_class; a root cause may only be attached to a failure"
            )
        if self.fault_class_confirmed and self.fault_class is None:
            raise ValueError("fault_class_confirmed=True requires a fault_class")
        return self

    @classmethod
    def from_checks(
        cls,
        *,
        node_id: str,
        component: str,
        results: Sequence[CheckResult],
        subgoal_id: Optional[str] = None,
        self_reported_ok: bool = True,
        fault_class: Optional[FaultClass] = None,
        fault_class_confirmed: bool = False,
        detail: str = "",
    ) -> "ComponentOutcome":
        """Derive the verdict from the checks that were actually evaluated.

        The precedence is the whole point:

        * any decided ``FAIL`` -> ``FAILURE``;
        * else an adapter-level error -> ``FAILURE`` (a crash is a decided
          failure even when no check ran);
        * else *no check reached a verdict* -> ``UNDETERMINED``. This is the
          branch that stops an unavailable evaluator from minting reliability;
        * else ``SUCCESS``.
        """
        decided = [r for r in results if r.decided]
        if any(r.status is CheckStatus.FAIL for r in decided):
            outcome = Outcome.FAILURE
        elif not self_reported_ok:
            outcome = Outcome.FAILURE
        elif not decided:
            outcome = Outcome.UNDETERMINED
        else:
            outcome = Outcome.SUCCESS
        return cls(
            node_id=node_id,
            component=component,
            subgoal_id=subgoal_id,
            outcome=outcome,
            self_reported_ok=self_reported_ok,
            fault_class=fault_class if outcome is Outcome.FAILURE else None,
            fault_class_confirmed=fault_class_confirmed if outcome is Outcome.FAILURE else False,
            checks_consulted=[r.check_id for r in results],
            detail=detail,
        )


class EdgeOutcome(BaseModel):
    """One typed handoff between two components, judged.

    The statistics key is ``(producer, consumer, artifact_type)`` because that
    triple is what the compiler actually asks about: "has this pair ever moved
    a *GeneSet* between them successfully?" Two components can be reliable
    individually and still fail to compose over a particular artifact type —
    which is precisely the empirical fact tag-overlap matching could not see.
    """

    model_config = ConfigDict(extra="forbid")

    #: Component names, not node ids.
    producer: str
    consumer: str
    artifact_type: str
    outcome: Outcome
    #: Static verdict recorded at the edge, when one was computed.
    compatibility: Optional[Compatibility] = None
    producer_node: str = ""
    consumer_node: str = ""
    artifact_id: str = ""
    detail: str = ""

    @model_validator(mode="after")
    def _unproven_compatibility_is_never_success(self) -> "EdgeOutcome":
        """``UNDERSPECIFIED`` must not accumulate into evidence that the edge works.

        If the producer never declared a facet the consumer requires, then a
        run that happened not to crash tells us nothing about whether the
        consumer interpreted the payload correctly. Recording that as a
        success is how a system learns to trust an edge it never verified.
        """
        unproven = (Compatibility.UNDERSPECIFIED, Compatibility.INCOMPATIBLE)
        if self.outcome is Outcome.SUCCESS and self.compatibility in unproven:
            raise ValueError(
                f"edge {self.producer}->{self.consumer} ({self.artifact_type}) is "
                f"{self.compatibility.value if self.compatibility else 'unknown'}; a handoff whose "
                "compatibility was never established cannot be recorded as a success "
                "(use Outcome.UNDETERMINED)"
            )
        return self


class RepairAttempt(BaseModel):
    """One patch, its shadow verdict, and whether it actually helped.

    Success is *derived*, never asserted, and it is deliberately strict: a
    patch that makes the original failure go away while regressing a
    previously passing check is a failed repair. Counting it as a success is
    how "repair" degenerates into "make the symptom disappear".
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    #: The hypothesis the patch was acting on.
    fault_class: FaultClass
    patch_family: str
    #: Tier the loop was operating at. May sit *above* the taxonomy's default
    #: tier for this fault class, because a recurring fault escalates.
    tier: Optional[RepairTier] = None
    committed: bool = False
    fixed_original_failure: bool = False
    #: Checks that passed before the patch and failed after it.
    collateral_regressions: list[str] = Field(default_factory=list)
    #: False when the patch's effect was never measured — the component was
    #: not shadow-safe, or the shadow budget was exhausted. Then we do not
    #: know whether it worked, and it contributes no statistical evidence.
    verified: bool = True
    detail: str = ""

    @property
    def outcome(self) -> Outcome:
        if not self.verified:
            return Outcome.UNDETERMINED
        if not self.committed:
            # Proposed, measured, and rejected: real negative evidence about
            # this (fault class, patch family) pair.
            return Outcome.FAILURE
        if not self.fixed_original_failure:
            return Outcome.FAILURE
        if self.collateral_regressions:
            return Outcome.FAILURE
        return Outcome.SUCCESS

    @model_validator(mode="after")
    def _admissible_and_consistent(self) -> "RepairAttempt":
        spec = FAULT_TAXONOMY[self.fault_class]
        if self.patch_family not in spec.admissible_patches:
            raise ValueError(
                f"patch family '{self.patch_family}' is not admissible for fault class "
                f"'{self.fault_class.value}' (admissible: "
                f"{', '.join(spec.admissible_patches)}); recording it would let the "
                "statistics justify a repair the taxonomy forbids"
            )
        if not self.verified and self.fixed_original_failure:
            raise ValueError(
                "fixed_original_failure=True requires verified=True; an unmeasured "
                "patch cannot be known to have fixed anything"
            )
        if self.tier is not None and self.tier is not spec.tier:
            raise ValueError(
                f"repair tier '{self.tier.value}' contradicts the taxonomy tier "
                f"'{spec.tier.value}' for fault class '{self.fault_class.value}'"
            )
        return self


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------


class RunRecord(BaseModel):
    """Everything one execution taught us.

    ``components``/``handoffs``/``repairs`` are the *only* inputs to the
    statistics layer. Statistics deliberately never infers component outcomes
    from :attr:`checks`: a check's subject is a node id, and mapping node ids
    to components requires the workflow. Guessing there would reintroduce
    exactly the kind of heuristic association this rebuild removed.

    There is no timestamp field, by design. Nothing in a decision path may
    depend on wall-clock time; ordering is carried by the append order of the
    JSONL file.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str
    workflow_id: str = ""
    #: ``CompiledWorkflow.structural_key()`` — lets statistics be grouped by
    #: topology rather than by workflow instance.
    structural_key: str = ""
    utility: UtilityVector = Field(default_factory=lambda: UtilityVector.of())
    checks: CheckReport = Field(default_factory=CheckReport)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    repairs: list[RepairAttempt] = Field(default_factory=list)
    cost: CostProfile = Field(default_factory=CostProfile)
    status: RunStatus = RunStatus.COMPLETED
    components: list[ComponentOutcome] = Field(default_factory=list)
    handoffs: list[EdgeOutcome] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _identified(self) -> "RunRecord":
        if not self.run_id:
            raise ValueError("run record requires a non-empty run_id")
        if not self.task_id:
            raise ValueError("run record requires a non-empty task_id")
        return self

    # -- honest summaries ---------------------------------------------------

    @property
    def hard_validity(self) -> Outcome:
        """Did the hard contract actually hold?

        ``UNDETERMINED`` when no HARD check reached a verdict. A run that
        terminated cleanly with every evaluator unavailable has demonstrated
        nothing, and this property refuses to say otherwise.
        """
        hard = self.checks.by_level(CheckLevel.HARD)
        decided = [r for r in hard if r.decided]
        if not decided:
            return Outcome.UNDETERMINED
        if any(r.status is CheckStatus.FAIL for r in decided):
            return Outcome.FAILURE
        return Outcome.SUCCESS

    @property
    def silent_failures(self) -> list[ComponentOutcome]:
        return [c for c in self.components if c.silent]

    def component_names(self) -> list[str]:
        return sorted({c.component for c in self.components})

    # -- canonical serialization -------------------------------------------

    def canonical_dict(self) -> dict[str, Any]:
        """JSON-ready payload with every set rendered in a stable order.

        ``UtilityVector.unavailable`` is a ``set``; Python set iteration order
        for strings varies with the interpreter's hash seed, so serializing it
        raw would make two identical histories produce different bytes.
        """
        payload = self.model_dump(mode="json")
        utility = payload.get("utility")
        if isinstance(utility, dict) and isinstance(utility.get("unavailable"), list):
            utility["unavailable"] = sorted(utility["unavailable"])
        return payload

    def to_json_line(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "RunRecord":
        return cls.model_validate(json.loads(line))


# ---------------------------------------------------------------------------
# Append-only store
# ---------------------------------------------------------------------------


class RunStore:
    """JSONL-backed, append-only history of runs.

    Append-only is not an implementation detail: a repair loop that could
    rewrite the record of the run it is repairing would be able to erase the
    evidence against its own patch. ``append`` refuses to overwrite an
    existing ``run_id``.

    A store with ``path=None`` is purely in-memory, which is what tests and
    single-shot compilations want.
    """

    def __init__(self, path: str | Path | None = None, *, load_existing: bool = True) -> None:
        self._path: Optional[Path] = Path(path) if path is not None else None
        self._records: list[RunRecord] = []
        self._index: dict[str, int] = {}
        if self._path is not None and load_existing and self._path.exists():
            self._read()

    # -- construction -------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "RunStore":
        return cls(path, load_existing=True)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    # -- writing ------------------------------------------------------------

    def append(self, record: RunRecord) -> None:
        if record.run_id in self._index:
            raise ValueError(
                f"run '{record.run_id}' is already recorded; the run store is append-only "
                "and does not overwrite history"
            )
        line = record.to_json_line()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        self._index[record.run_id] = len(self._records)
        self._records.append(record)

    def extend(self, records: Iterable[RunRecord]) -> None:
        for record in records:
            self.append(record)

    # -- reading ------------------------------------------------------------

    def _read(self) -> None:
        assert self._path is not None
        text = self._path.read_text(encoding="utf-8")
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = RunRecord.from_json_line(line)
            except Exception as exc:  # pragma: no cover - exercised via tests
                # Skipping a corrupt line silently would be the same mistake as
                # treating an unavailable evaluator as a pass: it hides missing
                # evidence behind an apparently healthy store.
                raise ValueError(f"{self._path}:{lineno}: malformed run record: {exc}") from exc
            if record.run_id in self._index:
                raise ValueError(
                    f"{self._path}:{lineno}: duplicate run_id '{record.run_id}' in run store"
                )
            self._index[record.run_id] = len(self._records)
            self._records.append(record)

    def all(self) -> list[RunRecord]:
        """Every record, in append order."""
        return list(self._records)

    def by_task(self, task_id: str) -> list[RunRecord]:
        return [r for r in self._records if r.task_id == task_id]

    def by_workflow(self, workflow_id: str) -> list[RunRecord]:
        return [r for r in self._records if r.workflow_id == workflow_id]

    def by_structural_key(self, structural_key: str) -> list[RunRecord]:
        return [r for r in self._records if r.structural_key == structural_key]

    def get(self, run_id: str) -> Optional[RunRecord]:
        idx = self._index.get(run_id)
        return None if idx is None else self._records[idx]

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[RunRecord]:
        return iter(self._records)

    def __contains__(self, run_id: object) -> bool:
        return isinstance(run_id, str) and run_id in self._index


__all__ = [
    "Outcome",
    "RunStatus",
    "ComponentOutcome",
    "EdgeOutcome",
    "RepairAttempt",
    "RunRecord",
    "RunStore",
]
