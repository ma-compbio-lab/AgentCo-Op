"""Benchmark tasks, and the environment one materializes into.

The design constraint that shapes this whole package: a benchmark on which
"build a multi-agent workflow" is always the right answer cannot measure
whether a system *knows when* to build one. So every task declares a
``regime``, and one of the three regimes is ``multi_harmful`` — tasks where a
single component is not merely sufficient but strictly better, and where
composing is a measurable mistake. A system that scores well on
``multi_necessary`` and badly on ``multi_harmful`` has learned to compose, not
to decide.

The second constraint is that blame has to be checkable. A task carries the
faults injected into it and, for each, the ground-truth blame target. Reporting
"our diagnoser localizes well" is otherwise unfalsifiable, since the only
witness would be the diagnoser itself.

Tasks are declarative. Nothing here executes; :func:`build_environment` turns
the declaration into adapters, cards, and a type registry, and the harness is
what runs it. That separation means a suite can be inspected, diffed, and
reviewed without a runtime.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.components.base import AdapterRegistry, Clock, zero_clock
from agentcoop.ir.artifacts import Artifact, ArtifactType, TypeRegistry
from agentcoop.ir.capability import CapabilityCard, ComponentLibrary
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.faults import FaultClass

Regime = Literal["single_sufficient", "multi_necessary", "multi_harmful"]
Horizon = Literal["short", "medium", "long"]

#: Builds an adapter from a behaviour name and its parameters. Registered in
#: :mod:`agentcoop.bench.behaviors` so a suite stays pure data.
AdapterFactory = Callable[..., Any]


class GroundTruth(BaseModel):
    """What a correct answer looks like, stated independently of any system.

    ``minimal_component_set`` is the honest centre of this model: the smallest
    set of components that can actually discharge the task. In the
    ``multi_harmful`` regime it has exactly one member, and a system that uses
    more has failed even if its output is correct — it spent budget and added
    failure surface for nothing.
    """

    model_config = ConfigDict(extra="forbid")

    #: Artifact type name -> the payload a correct run must produce. Compared
    #: structurally, so a component that reorders a list still matches.
    expected_outputs: dict[str, Any] = Field(default_factory=dict)
    #: Fields within an expected output that must match exactly. Empty means
    #: the whole payload is compared.
    key_fields: dict[str, list[str]] = Field(default_factory=dict)
    #: Smallest component set that can discharge the task.
    minimal_component_set: list[str] = Field(default_factory=list)
    #: Subgoals that genuinely cannot be served by the same component as their
    #: neighbours — the reason composition is or is not necessary.
    irreducible_subgoals: list[str] = Field(default_factory=list)
    #: Set when a scalar metric genuinely does not exist for this task, so the
    #: harness reports contract-level results rather than inventing a score.
    has_scalar_oracle: bool = True
    notes: list[str] = Field(default_factory=list)

    @property
    def minimal_size(self) -> int:
        return len(set(self.minimal_component_set))


class ComponentSpec(BaseModel):
    """A component as data: its card, plus how to make it behave.

    The card here is the *declared* one. Whether the benchmark hands a system
    the declared card or a probed one is a property of the run, not of the
    task, because "does certification help?" is one of the questions the
    harness exists to answer.
    """

    model_config = ConfigDict(extra="forbid")

    card: CapabilityCard
    #: Name of a behaviour registered in :mod:`agentcoop.bench.behaviors`.
    behavior: str = "passthrough"
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.card.name


class InjectedFault(BaseModel):
    """A deliberate defect, with the blame target recorded up front."""

    model_config = ConfigDict(extra="forbid")

    fault_id: str
    fault_class: FaultClass
    #: Component name, subgoal id, or edge the fault is planted in.
    target: str
    #: Name of a mechanism in :mod:`agentcoop.bench.faults`.
    mechanism: str
    #: What a correct diagnosis should blame. Written when the fault is
    #: authored, never derived from a run.
    expected_blame: str
    params: dict[str, Any] = Field(default_factory=dict)
    #: Whether the fault is designed to be invisible to exit codes. Silent
    #: faults are scored separately: catching one is the harder result.
    silent: bool = False
    description: str = ""


class BenchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    description: str = ""
    dossier: TaskEvidenceDossier
    component_specs: list[ComponentSpec] = Field(default_factory=list)
    #: Starting artifacts, keyed by artifact type name.
    inputs: dict[str, Artifact] = Field(default_factory=dict)
    ground_truth: Optional[GroundTruth] = None
    injected_faults: list[InjectedFault] = Field(default_factory=list)
    regime: Regime = "multi_necessary"
    horizon: Horizon = "short"
    tags: list[str] = Field(default_factory=list)

    def spec(self, name: str) -> Optional[ComponentSpec]:
        for s in self.component_specs:
            if s.name == name:
                return s
        return None

    @property
    def component_names(self) -> list[str]:
        return [s.name for s in self.component_specs]

    @property
    def artifact_types(self) -> list[ArtifactType]:
        return list(self.dossier.artifact_types)

    def faults_targeting(self, target: str) -> list[InjectedFault]:
        return [f for f in self.injected_faults if f.target == target]

    def expected_blame_targets(self) -> list[str]:
        return [f.expected_blame for f in self.injected_faults]


class BenchSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    description: str = ""
    tasks: list[BenchTask] = Field(default_factory=list)

    def by_regime(self, regime: Regime) -> list[BenchTask]:
        return [t for t in self.tasks if t.regime == regime]

    def by_horizon(self, horizon: Horizon) -> list[BenchTask]:
        return [t for t in self.tasks if t.horizon == horizon]

    def with_faults(self) -> list[BenchTask]:
        return [t for t in self.tasks if t.injected_faults]

    def task(self, task_id: str) -> Optional[BenchTask]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def regime_coverage(self) -> dict[str, int]:
        """Task count per regime.

        A suite missing ``multi_harmful`` cannot support the central negative
        claim, so this is checked rather than assumed — see
        :meth:`coverage_defects`.
        """
        counts = {"single_sufficient": 0, "multi_necessary": 0, "multi_harmful": 0}
        for t in self.tasks:
            counts[t.regime] = counts.get(t.regime, 0) + 1
        return counts

    def coverage_defects(self) -> list[str]:
        """Ways this suite would fail to support the claims made from it."""
        defects: list[str] = []
        counts = self.regime_coverage()
        for regime, n in sorted(counts.items()):
            if n == 0:
                defects.append(
                    f"no '{regime}' tasks: results from this suite cannot speak to "
                    "whether the system decides correctly in that regime"
                )
        if not self.with_faults():
            defects.append(
                "no task carries an injected fault, so localization and diagnosis "
                "accuracy are not measurable from this suite"
            )
        silent = [f for t in self.tasks for f in t.injected_faults if f.silent]
        if not silent:
            defects.append(
                "every injected fault is loud; a suite with no silent faults "
                "measures crash handling rather than failure detection"
            )
        unlabelled = [t.task_id for t in self.tasks if t.ground_truth is None]
        if unlabelled:
            defects.append(
                "tasks without ground truth cannot be scored: "
                + ", ".join(sorted(unlabelled)[:5])
            )
        return defects


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


class BenchEnvironment(BaseModel):
    """Everything needed to run one task, built from its declaration."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    task: BenchTask
    adapters: AdapterRegistry
    library: ComponentLibrary
    types: TypeRegistry
    #: Faults actually installed, and what each is expected to blame.
    active_faults: list[InjectedFault] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def blame_key(self) -> dict[str, str]:
        """fault_id -> expected blame target. The answer sheet."""
        return {f.fault_id: f.expected_blame for f in self.active_faults}


def build_type_registry(task: BenchTask) -> TypeRegistry:
    registry = TypeRegistry()
    for artifact_type in task.dossier.artifact_types:
        registry.register_type(artifact_type)
    return registry


def build_library(task: BenchTask, *, cards: Optional[list[CapabilityCard]] = None) -> ComponentLibrary:
    library = ComponentLibrary()
    for card in cards if cards is not None else [s.card for s in task.component_specs]:
        library.add(card.model_copy(deep=True))
    return library


def build_environment(
    task: BenchTask,
    *,
    inject_faults: bool = True,
    clock: Clock = zero_clock,
) -> BenchEnvironment:
    """Turn a declared task into a runnable environment.

    ``clock`` defaults to a clock that never advances so that a benchmark run
    is byte-reproducible. Wall-clock cost is measured by the harness at a
    level where it cannot leak into any decision.
    """
    from agentcoop.bench.behaviors import build_adapter
    from agentcoop.bench.faults import install_faults

    registry = AdapterRegistry()
    for spec in task.component_specs:
        registry.register(build_adapter(spec, clock=clock))

    env = BenchEnvironment(
        task=task,
        adapters=registry,
        library=build_library(task),
        types=build_type_registry(task),
    )
    if inject_faults and task.injected_faults:
        env = install_faults(env, task.injected_faults)
    return env


__all__ = [
    "Regime",
    "Horizon",
    "GroundTruth",
    "ComponentSpec",
    "InjectedFault",
    "BenchTask",
    "BenchSuite",
    "BenchEnvironment",
    "build_environment",
    "build_library",
    "build_type_registry",
    "AdapterFactory",
    "FaultClass",
]
