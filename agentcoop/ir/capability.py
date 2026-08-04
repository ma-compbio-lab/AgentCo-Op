"""Capability cards — what a component can *demonstrably* do.

A capability card is deliberately split into two halves:

* **Declared** — read off a README, a manifest, or an LLM's summary of a
  repository. Cheap, and untrustworthy.
* **Empirical** — populated only by executing probes against the component.
  Expensive, and the only thing the compiler is allowed to rely on for
  anything consequential.

``certification_level`` is computed from the empirical half, never asserted.
The compiler refuses to bind a component to a subgoal whose required
certification exceeds what the component has actually earned. That single
rule is what replaces "the designer agent decided TissueAgent looks relevant".
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import ArtifactType, FacetSet
from agentcoop.ir.faults import FaultClass


class CertificationLevel(IntEnum):
    """How much we actually know about a component. Ordered."""

    #: Nothing but a name. Cannot be bound to anything.
    UNKNOWN = 0
    #: Contracts read from README/manifest/LLM inference. No execution.
    DECLARED = 1
    #: The component was invoked and its entrypoint is reachable.
    REACHABLE = 2
    #: Probes confirm it consumes the declared input and emits the declared
    #: output artifact with the declared facets.
    PROBED = 3
    #: Probes plus negative tests: it rejects invalid input rather than
    #: silently emitting plausible garbage, and respects resource limits.
    CERTIFIED = 4
    #: Certified plus a reliability history over real runs of this task family.
    TRUSTED = 5


class ComponentKind(str, Enum):
    LLM = "llm"
    PYTHON_FUNCTION = "python_function"
    SUBPROCESS = "subprocess"
    CONTAINER = "container"
    EXTERNAL_REPO = "external_repo"
    #: A general-purpose coding agent (Codex, Claude Code) used as an
    #: executor backend rather than as a competitor.
    CODING_AGENT = "coding_agent"
    HUMAN = "human"
    #: A pure, deterministic converter/adapter.
    ADAPTER = "adapter"
    EVALUATOR = "evaluator"


class IOContract(BaseModel):
    """What a component consumes and produces, in typed-artifact terms."""

    model_config = ConfigDict(extra="forbid")

    consumes: list[ArtifactType] = Field(default_factory=list)
    produces: list[ArtifactType] = Field(default_factory=list)
    #: Parameters accepted beyond the artifacts themselves.
    parameters: dict[str, Any] = Field(default_factory=dict)

    def produced_type(self, name: str) -> Optional[ArtifactType]:
        for t in self.produces:
            if t.name == name:
                return t
        return None

    def consumed_type(self, name: str) -> Optional[ArtifactType]:
        for t in self.consumes:
            if t.name == name:
                return t
        return None


class EnvironmentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python: Optional[str] = None
    r: Optional[str] = None
    cuda: Optional[str] = None
    system_packages: list[str] = Field(default_factory=list)
    pip_packages: list[str] = Field(default_factory=list)
    r_packages: list[str] = Field(default_factory=list)
    container_image: Optional[str] = None
    network: bool = False
    min_memory_gb: float = 1.0
    gpu_required: bool = False

    def conflicts_with(self, other: "EnvironmentContract") -> list[str]:
        """Detect conflicts that make co-location in one environment unsafe.

        Used by static analysis: two components that pin incompatible runtimes
        must be isolated, and the compiler needs to know that *before* the run
        rather than after a ``ModuleNotFoundError`` mid-workflow.
        """
        problems: list[str] = []
        for field_name in ("python", "r", "cuda"):
            mine = getattr(self, field_name)
            theirs = getattr(other, field_name)
            if mine and theirs and mine != theirs:
                problems.append(f"{field_name} pin conflict: {mine} vs {theirs}")
        mine_pins = _pin_map(self.pip_packages)
        their_pins = _pin_map(other.pip_packages)
        for pkg, version in mine_pins.items():
            if pkg in their_pins and their_pins[pkg] != version:
                problems.append(f"pip pin conflict on {pkg}: {version} vs {their_pins[pkg]}")
        return problems


def _pin_map(specs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in specs:
        if "==" in spec:
            name, _, version = spec.partition("==")
            out[name.strip().lower()] = version.strip()
    return out


class BehaviorContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deterministic: bool = False
    idempotent: bool = False
    retry_safe: bool = True
    #: Side effects outside the artifact store (writes files, calls paid APIs,
    #: mutates a database). Governs whether shadow validation is even legal.
    side_effects: list[str] = Field(default_factory=list)
    #: Whether it is safe to run this component speculatively on reduced data.
    shadow_safe: bool = True


class CostProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_s: float = 0.0
    tokens: int = 0
    usd: float = 0.0
    cpu_seconds: float = 0.0
    gpu_seconds: float = 0.0
    peak_memory_gb: float = 0.0

    def __add__(self, other: "CostProfile") -> "CostProfile":
        return CostProfile(
            latency_s=self.latency_s + other.latency_s,
            tokens=self.tokens + other.tokens,
            usd=self.usd + other.usd,
            cpu_seconds=self.cpu_seconds + other.cpu_seconds,
            gpu_seconds=self.gpu_seconds + other.gpu_seconds,
            peak_memory_gb=max(self.peak_memory_gb, other.peak_memory_gb),
        )

    def scaled(self, factor: float) -> "CostProfile":
        return CostProfile(
            latency_s=self.latency_s * factor,
            tokens=int(self.tokens * factor),
            usd=self.usd * factor,
            cpu_seconds=self.cpu_seconds * factor,
            gpu_seconds=self.gpu_seconds * factor,
            peak_memory_gb=self.peak_memory_gb,
        )


class FailureSignature(BaseModel):
    """A known way this component fails, and how to recognise it.

    Silent failures matter most: a component that returns an empty gene set
    on malformed input, with exit code 0, is far more dangerous than one that
    crashes.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    pattern: str = ""
    fault_class: FaultClass = FaultClass.TOOL_FAILURE
    silent: bool = False
    note: str = ""


class ReliabilityPosterior(BaseModel):
    """Beta posterior over a component's success probability.

    Starts at Beta(1,1) — a genuinely uninformative prior — so a component
    with no history is never mistaken for a reliable one.
    """

    model_config = ConfigDict(extra="forbid")

    alpha: float = 1.0
    beta: float = 1.0

    def update(self, *, successes: int = 0, failures: int = 0) -> "ReliabilityPosterior":
        return ReliabilityPosterior(alpha=self.alpha + successes, beta=self.beta + failures)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def n_observations(self) -> float:
        return self.alpha + self.beta - 2.0

    @property
    def stddev(self) -> float:
        a, b = self.alpha, self.beta
        return ((a * b) / ((a + b) ** 2 * (a + b + 1))) ** 0.5

    def credible_interval(self, mass: float = 0.9) -> tuple[float, float]:
        """Normal-approximation interval, clamped to [0, 1].

        Exact Beta quantiles would need scipy; the approximation is adequate
        for ranking components and keeps the dependency footprint small.
        """
        from statistics import NormalDist

        z = NormalDist().inv_cdf(0.5 + mass / 2.0)
        lo = max(0.0, self.mean - z * self.stddev)
        hi = min(1.0, self.mean + z * self.stddev)
        return lo, hi

    #: Lower confidence bound — used for ranking so that a component with one
    #: lucky success does not outrank one with a long solid record.
    @property
    def lcb(self) -> float:
        return self.credible_interval(0.9)[0]


class ProbeOutcome(BaseModel):
    """Result of one executed probe. Written only by the probe runner."""

    model_config = ConfigDict(extra="forbid")

    probe_id: str
    kind: str
    passed: bool
    duration_s: float = 0.0
    detail: str = ""
    #: Facets the probe actually *observed*, keyed by artifact type name — the
    #: same shape as :attr:`EmpiricalRecord.confirmed_facets`, which is where
    #: certification promotes them. These override declared facets, because
    #: observation beats documentation. Keying by type rather than flattening
    #: matters for multi-output components, where two artifacts can legitimately
    #: carry different values for the same facet key.
    observed_facets: dict[str, FacetSet] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class EmpiricalRecord(BaseModel):
    """Everything we learned by actually running the component."""

    model_config = ConfigDict(extra="forbid")

    probes: list[ProbeOutcome] = Field(default_factory=list)
    reliability: ReliabilityPosterior = Field(default_factory=ReliabilityPosterior)
    observed_cost: Optional[CostProfile] = None
    #: Facets confirmed by observation, keyed by artifact type name.
    confirmed_facets: dict[str, FacetSet] = Field(default_factory=dict)
    last_probed_commit: Optional[str] = None

    def probes_of(self, kind: str) -> list[ProbeOutcome]:
        return [p for p in self.probes if p.kind == kind]

    def passed(self, kind: str) -> bool:
        outcomes = self.probes_of(kind)
        return bool(outcomes) and all(p.passed for p in outcomes)


class TrustMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: Optional[str] = None
    commit: Optional[str] = None
    license: Optional[str] = None
    maintained: Optional[bool] = None
    expert_validated: bool = False
    notes: list[str] = Field(default_factory=list)


class CapabilityCard(BaseModel):
    """The full record for one composable component."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: ComponentKind
    description: str = ""
    #: Subgoal *kinds* this component claims to serve, e.g.
    #: ``["differential_expression", "gene_set_interpretation"]``. Claims are
    #: only load-bearing once probes confirm the corresponding contract.
    functional_capabilities: list[str] = Field(default_factory=list)
    io: IOContract = Field(default_factory=IOContract)
    environment: EnvironmentContract = Field(default_factory=EnvironmentContract)
    behavior: BehaviorContract = Field(default_factory=BehaviorContract)
    declared_cost: CostProfile = Field(default_factory=CostProfile)
    failure_profile: list[FailureSignature] = Field(default_factory=list)
    empirical: EmpiricalRecord = Field(default_factory=EmpiricalRecord)
    trust: TrustMetadata = Field(default_factory=TrustMetadata)
    #: Free-form adapter routing info (entrypoint, module path, image tag).
    binding: dict[str, Any] = Field(default_factory=dict)

    # -- certification ------------------------------------------------------

    @property
    def certification_level(self) -> CertificationLevel:
        """Computed, never assigned.

        The ladder is strict: each level requires everything below it. A
        component cannot be CERTIFIED on the strength of a passing smoke test
        alone, because the property that matters most — refusing bad input
        instead of silently succeeding — is only established by the negative
        probes.
        """
        emp = self.empirical
        if not self.io.produces and not self.io.consumes:
            return CertificationLevel.UNKNOWN
        if not emp.probes:
            return CertificationLevel.DECLARED
        if not emp.passed("reachable"):
            return CertificationLevel.DECLARED
        if not (emp.passed("schema") and emp.passed("smoke")):
            return CertificationLevel.REACHABLE
        if not (emp.passed("invalid_input") and emp.passed("resource")):
            return CertificationLevel.PROBED
        if emp.reliability.n_observations >= 5 and emp.reliability.lcb >= 0.6:
            return CertificationLevel.TRUSTED
        return CertificationLevel.CERTIFIED

    def effective_facets(self, artifact_type_name: str) -> FacetSet:
        """Declared facets, overridden by anything a probe actually observed."""
        declared = {}
        produced = self.io.produced_type(artifact_type_name)
        if produced is not None:
            declared = dict(produced.facets)
        declared.update(self.empirical.confirmed_facets.get(artifact_type_name, {}))
        return declared

    def output_type(self, name: str) -> Optional[ArtifactType]:
        """Produced artifact type with observed facets folded in."""
        base = self.io.produced_type(name)
        if base is None:
            return None
        return base.model_copy(update={"facets": self.effective_facets(name)})

    def can_produce(self, artifact_type_name: str) -> bool:
        return self.io.produced_type(artifact_type_name) is not None

    def can_consume(self, artifact_type_name: str) -> bool:
        return self.io.consumed_type(artifact_type_name) is not None

    def expected_cost(self) -> CostProfile:
        return self.empirical.observed_cost or self.declared_cost

    def has_silent_failure_mode(self) -> bool:
        return any(sig.silent for sig in self.failure_profile)


class ComponentLibrary(BaseModel):
    """A set of capability cards available for composition."""

    model_config = ConfigDict(extra="forbid")

    cards: dict[str, CapabilityCard] = Field(default_factory=dict)

    def add(self, card: CapabilityCard) -> CapabilityCard:
        self.cards[card.name] = card
        return card

    def get(self, name: str) -> Optional[CapabilityCard]:
        return self.cards.get(name)

    def require(self, name: str) -> CapabilityCard:
        card = self.cards.get(name)
        if card is None:
            raise KeyError(f"component '{name}' is not in the library")
        return card

    def names(self) -> list[str]:
        return sorted(self.cards)

    def producing(self, artifact_type_name: str) -> list[CapabilityCard]:
        return [c for c in self.cards.values() if c.can_produce(artifact_type_name)]

    def with_capability(self, capability: str) -> list[CapabilityCard]:
        return [c for c in self.cards.values() if capability in c.functional_capabilities]

    def certified_at_least(self, level: CertificationLevel) -> list[CapabilityCard]:
        return [c for c in self.cards.values() if c.certification_level >= level]


__all__ = [
    "CertificationLevel",
    "ComponentKind",
    "IOContract",
    "EnvironmentContract",
    "BehaviorContract",
    "CostProfile",
    "FailureSignature",
    "ReliabilityPosterior",
    "ProbeOutcome",
    "EmpiricalRecord",
    "TrustMetadata",
    "CapabilityCard",
    "ComponentLibrary",
]
