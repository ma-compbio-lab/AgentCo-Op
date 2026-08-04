"""Reliability statistics over recorded outcomes.

Every quantity here is a Beta posterior with a genuinely uninformative
Beta(1,1) prior (see :class:`~agentcoop.ir.capability.ReliabilityPosterior`).
That choice is doing real work: a component nobody has ever run has posterior
mean 0.5 and a 90% lower bound near 0.02, so it can never be mistaken for a
reliable one, and it can never win a ranking on the strength of having no
recorded failures.

Three keyings, because these are the three questions the compiler actually
asks and they have different answers:

``component``
    Does this component do its job?
``(producer, consumer, artifact_type)``
    Do these two *compose* over this artifact type? Two individually reliable
    components can still fail to hand off a GeneSet between them, and that
    fact is invisible to any per-component statistic.
``(fault_class, patch_family)``
    Does this repair family actually fix this root cause? This is what stops
    "test failed -> retry the node" from being reinforced by the runs where
    the retry happened to work.

Ranking is by lower confidence bound, never by mean: one lucky success gives
Beta(2,1), mean 0.67 — higher than a component with 8 successes and 4 failures
(mean 0.64) — but a far worse lower bound (0.28 vs 0.44). The LCB is what
encodes "we have not seen enough of you yet".

``UNDETERMINED`` observations move neither ``alpha`` nor ``beta``. They are
counted separately and surfaced, so a component with ten unevaluable runs is
reported as *unknown*, not as *good*.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.capability import ReliabilityPosterior
from agentcoop.ir.faults import FaultClass
from agentcoop.memory.store import (
    ComponentOutcome,
    EdgeOutcome,
    Outcome,
    RepairAttempt,
    RunRecord,
)

#: Additive (Laplace) smoothing constant for the fault-class distribution.
#: With no observations this yields a uniform distribution over the taxonomy,
#: which is the honest statement of "we have no idea how this component fails".
DEFAULT_LAPLACE_ALPHA = 1.0

#: Key for an edge posterior.
EdgeKey = tuple[str, str, str]
#: Key for a repair posterior.
RepairKey = tuple[FaultClass, str]


class ObservationCounts(BaseModel):
    """Success/failure/undetermined tallies behind one posterior.

    Kept explicit rather than folding straight into ``alpha``/``beta`` so that
    the number of observations we could *not* evaluate stays visible. That
    number is what tells a reader whether a mean of 1.0 means "always worked"
    or "nobody ever checked".
    """

    model_config = ConfigDict(extra="forbid")

    successes: int = 0
    failures: int = 0
    undetermined: int = 0

    @property
    def n(self) -> int:
        """Observations that reached a verdict. The only ones that are evidence."""
        return self.successes + self.failures

    @property
    def n_total(self) -> int:
        return self.n + self.undetermined

    @property
    def posterior(self) -> ReliabilityPosterior:
        return ReliabilityPosterior().update(successes=self.successes, failures=self.failures)

    def observe(self, outcome: Outcome) -> None:
        if outcome is Outcome.SUCCESS:
            self.successes += 1
        elif outcome is Outcome.FAILURE:
            self.failures += 1
        else:
            self.undetermined += 1


class EdgeStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    producer: str
    consumer: str
    artifact_type: str
    counts: ObservationCounts = Field(default_factory=ObservationCounts)


class RepairStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_class: FaultClass
    patch_family: str
    counts: ObservationCounts = Field(default_factory=ObservationCounts)


class FailureCounts(BaseModel):
    """Observed fault classes for one component.

    ``confirmed`` is the subset where a repair admissible for that class
    actually fixed the failure. Keeping the two apart matters because these
    counts feed back into diagnosis as priors: without the distinction, one
    confident-but-wrong diagnosis would raise the prior for the wrong class and
    make the next diagnosis more likely to repeat it.
    """

    model_config = ConfigDict(extra="forbid")

    component: str
    observed: dict[FaultClass, int] = Field(default_factory=dict)
    confirmed: dict[FaultClass, int] = Field(default_factory=dict)


class StatisticsSnapshot(BaseModel):
    """Serializable form of a :class:`StatisticsStore`.

    ``runs_observed`` is part of the snapshot so that reloading a snapshot and
    replaying the run log cannot double-count: aggregation must be idempotent
    per run, or a single re-import silently doubles everyone's confidence.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    components: dict[str, ObservationCounts] = Field(default_factory=dict)
    edges: list[EdgeStatistics] = Field(default_factory=list)
    repairs: list[RepairStatistics] = Field(default_factory=list)
    failures: list[FailureCounts] = Field(default_factory=list)
    runs_observed: list[str] = Field(default_factory=list)


class StatisticsStore:
    """Aggregated posteriors over recorded runs.

    Not a pydantic model: the edge and repair keys are tuples, which JSON
    cannot express as object keys. :meth:`snapshot` converts to a typed,
    losslessly round-trippable representation.
    """

    def __init__(self) -> None:
        self._components: dict[str, ObservationCounts] = {}
        self._edges: dict[EdgeKey, ObservationCounts] = {}
        self._repairs: dict[RepairKey, ObservationCounts] = {}
        self._observed_faults: dict[str, dict[FaultClass, int]] = {}
        self._confirmed_faults: dict[str, dict[FaultClass, int]] = {}
        self._runs_observed: set[str] = set()

    # -- construction -------------------------------------------------------

    @classmethod
    def from_runs(cls, records: Iterable[RunRecord]) -> "StatisticsStore":
        store = cls()
        store.observe_runs(records)
        return store

    # -- ingestion ----------------------------------------------------------

    def observe_run(self, record: RunRecord) -> None:
        """Fold one run into the posteriors. Idempotent per ``run_id``.

        Idempotence is a correctness requirement, not a nicety: the normal
        recovery path is "load the snapshot, then replay the JSONL", and
        without deduplication that path inflates every count it touches.
        """
        if record.run_id in self._runs_observed:
            return
        self._runs_observed.add(record.run_id)
        for component_outcome in record.components:
            self._observe_component(component_outcome)
        for handoff in record.handoffs:
            self._observe_edge(handoff)
        for repair in record.repairs:
            self._observe_repair(repair)

    def observe_runs(self, records: Iterable[RunRecord]) -> None:
        for record in records:
            self.observe_run(record)

    def has_observed(self, run_id: str) -> bool:
        return run_id in self._runs_observed

    @property
    def n_runs_observed(self) -> int:
        return len(self._runs_observed)

    def _observe_component(self, obs: ComponentOutcome) -> None:
        counts = self._components.setdefault(obs.component, ObservationCounts())
        counts.observe(obs.outcome)
        if obs.outcome is Outcome.FAILURE and obs.fault_class is not None:
            observed = self._observed_faults.setdefault(obs.component, {})
            observed[obs.fault_class] = observed.get(obs.fault_class, 0) + 1
            if obs.fault_class_confirmed:
                confirmed = self._confirmed_faults.setdefault(obs.component, {})
                confirmed[obs.fault_class] = confirmed.get(obs.fault_class, 0) + 1

    def _observe_edge(self, obs: EdgeOutcome) -> None:
        key: EdgeKey = (obs.producer, obs.consumer, obs.artifact_type)
        self._edges.setdefault(key, ObservationCounts()).observe(obs.outcome)

    def _observe_repair(self, obs: RepairAttempt) -> None:
        key: RepairKey = (obs.fault_class, obs.patch_family)
        self._repairs.setdefault(key, ObservationCounts()).observe(obs.outcome)

    # -- posteriors ---------------------------------------------------------

    def component_reliability(self, component: str) -> ReliabilityPosterior:
        """Beta(1,1) for a component with no history — never an optimistic guess."""
        return self.component_counts(component).posterior

    def edge_compatibility(
        self, producer: str, consumer: str, artifact_type: str
    ) -> ReliabilityPosterior:
        return self.edge_counts(producer, consumer, artifact_type).posterior

    def repair_success(
        self, fault_class: FaultClass, patch_family: str
    ) -> ReliabilityPosterior:
        return self.repair_counts(fault_class, patch_family).posterior

    # -- raw counts (needed by retrieval to state ``n`` honestly) -----------

    def component_counts(self, component: str) -> ObservationCounts:
        counts = self._components.get(component)
        return counts.model_copy(deep=True) if counts is not None else ObservationCounts()

    def edge_counts(
        self, producer: str, consumer: str, artifact_type: str
    ) -> ObservationCounts:
        counts = self._edges.get((producer, consumer, artifact_type))
        return counts.model_copy(deep=True) if counts is not None else ObservationCounts()

    def repair_counts(self, fault_class: FaultClass, patch_family: str) -> ObservationCounts:
        counts = self._repairs.get((fault_class, patch_family))
        return counts.model_copy(deep=True) if counts is not None else ObservationCounts()

    # -- fault-class distribution ------------------------------------------

    def failure_distribution(
        self,
        component: str,
        *,
        alpha: float = DEFAULT_LAPLACE_ALPHA,
        confirmed_only: bool = False,
    ) -> dict[FaultClass, float]:
        """Normalized, Laplace-smoothed distribution over :class:`FaultClass`.

        Every class receives positive mass. That is the point of the smoothing:
        used as a diagnostic prior, a zero would make a fault class
        *unreachable* no matter how strong the signal for it, so one unlucky
        history would permanently blind the diagnoser to a whole root cause.

        With no history the result is uniform — an explicit "no prior
        information" rather than a fabricated one. Keys are emitted in
        :class:`FaultClass` declaration order so the mapping is reproducible.
        """
        if alpha <= 0.0:
            raise ValueError("Laplace alpha must be positive to keep every class reachable")
        source = self._confirmed_faults if confirmed_only else self._observed_faults
        counts = source.get(component, {})
        classes = list(FaultClass)
        denominator = sum(counts.values()) + alpha * len(classes)
        return {fc: (counts.get(fc, 0) + alpha) / denominator for fc in classes}

    def failure_observations(self, component: str, *, confirmed_only: bool = False) -> int:
        """Number of failures with an attached root cause. The ``n`` behind the distribution."""
        source = self._confirmed_faults if confirmed_only else self._observed_faults
        return sum(source.get(component, {}).values())

    def observed_fault_counts(
        self, component: str, *, confirmed_only: bool = False
    ) -> dict[FaultClass, int]:
        source = self._confirmed_faults if confirmed_only else self._observed_faults
        return dict(source.get(component, {}))

    # -- ranking ------------------------------------------------------------

    def rank_components(
        self, components: Iterable[str]
    ) -> list[tuple[str, ReliabilityPosterior]]:
        """Rank by lower confidence bound, descending; ties broken by name.

        Deliberately not by mean. A single success gives a better mean than a
        long, mostly-good record, and selecting on that is how a component gets
        bound to a subgoal on the strength of one lucky run.
        """
        items = [(name, self.component_reliability(name)) for name in sorted(set(components))]
        return sorted(items, key=lambda kv: (-kv[1].lcb, kv[0]))

    # -- inventory ----------------------------------------------------------

    def known_components(self) -> list[str]:
        return sorted(self._components)

    def known_edges(self) -> list[EdgeKey]:
        return sorted(self._edges)

    def known_repairs(self) -> list[RepairKey]:
        return sorted(self._repairs, key=lambda k: (k[0].value, k[1]))

    # -- persistence --------------------------------------------------------

    def snapshot(self) -> StatisticsSnapshot:
        """Typed, deterministically ordered view of the aggregate state."""
        return StatisticsSnapshot(
            components={
                name: self._components[name].model_copy(deep=True)
                for name in sorted(self._components)
            },
            edges=[
                EdgeStatistics(
                    producer=producer,
                    consumer=consumer,
                    artifact_type=artifact_type,
                    counts=self._edges[(producer, consumer, artifact_type)].model_copy(deep=True),
                )
                for producer, consumer, artifact_type in self.known_edges()
            ],
            repairs=[
                RepairStatistics(
                    fault_class=fault_class,
                    patch_family=patch_family,
                    counts=self._repairs[(fault_class, patch_family)].model_copy(deep=True),
                )
                for fault_class, patch_family in self.known_repairs()
            ],
            failures=[
                FailureCounts(
                    component=name,
                    observed=_sorted_fault_counts(self._observed_faults.get(name, {})),
                    confirmed=_sorted_fault_counts(self._confirmed_faults.get(name, {})),
                )
                for name in sorted(set(self._observed_faults) | set(self._confirmed_faults))
            ],
            runs_observed=sorted(self._runs_observed),
        )

    @classmethod
    def from_snapshot(cls, snapshot: StatisticsSnapshot) -> "StatisticsStore":
        store = cls()
        store._components = {
            name: counts.model_copy(deep=True) for name, counts in snapshot.components.items()
        }
        store._edges = {
            (e.producer, e.consumer, e.artifact_type): e.counts.model_copy(deep=True)
            for e in snapshot.edges
        }
        store._repairs = {
            (r.fault_class, r.patch_family): r.counts.model_copy(deep=True)
            for r in snapshot.repairs
        }
        store._observed_faults = {f.component: dict(f.observed) for f in snapshot.failures}
        store._confirmed_faults = {f.component: dict(f.confirmed) for f in snapshot.failures}
        store._runs_observed = set(snapshot.runs_observed)
        return store

    def to_json(self) -> str:
        """Canonical JSON text. Byte-identical for equal aggregate states."""
        return json.dumps(self.snapshot().model_dump(mode="json"), sort_keys=True, indent=2) + "\n"

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "StatisticsStore":
        source = Path(path)
        if not source.exists():
            # An absent snapshot means "no history", which is a legitimate and
            # very common state; it must not look like a corrupted one.
            return cls()
        snapshot = StatisticsSnapshot.model_validate_json(source.read_text(encoding="utf-8"))
        return cls.from_snapshot(snapshot)


def _sorted_fault_counts(counts: dict[FaultClass, int]) -> dict[FaultClass, int]:
    """Re-key in taxonomy declaration order so serialization is reproducible."""
    return {fc: counts[fc] for fc in FaultClass if fc in counts}


__all__ = [
    "DEFAULT_LAPLACE_ALPHA",
    "EdgeKey",
    "RepairKey",
    "ObservationCounts",
    "EdgeStatistics",
    "RepairStatistics",
    "FailureCounts",
    "StatisticsSnapshot",
    "StatisticsStore",
]
