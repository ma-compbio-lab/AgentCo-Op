"""A synthetic suite covering all three regimes.

The tasks share one small domain — identifiers get mapped between namespaces,
mapped identifiers get looked up in a table, and results get summarized — but
the *shape* of each task differs deliberately:

``single_sufficient``
    One component covers every subgoal, and does it well. Composing works but
    buys nothing. A system that composes here has not failed the task; it has
    failed the decision.

``multi_necessary``
    The lookup component refuses identifiers in the source namespace, and the
    generalist is genuinely weak at the lookup subgoal. No single component
    can produce a correct answer, so composition is the only route.

``multi_harmful``
    Composition is available and *worse*: the mapping step is lossy, so
    routing through it discards data that the direct component would have
    kept. This is the case that makes "correctly declines to compose" a
    measurable success rather than a claim.

Every table here is small and explicit. The point of a synthetic suite is that
the right answer is knowable by construction — the moment the ground truth has
to be inferred from a model's output, the benchmark stops being able to say
who was right.
"""

from __future__ import annotations

from typing import Any, Optional

from agentcoop.bench.faults import Mechanism
from agentcoop.bench.task import (
    BenchSuite,
    BenchTask,
    ComponentSpec,
    GroundTruth,
    InjectedFault,
)
from agentcoop.components.base import make_artifact
from agentcoop.ir.artifacts import Artifact, ArtifactType
from agentcoop.ir.capability import (
    BehaviorContract,
    CapabilityCard,
    ComponentKind,
    CostProfile,
    IOContract,
)
from agentcoop.ir.checks import CheckLevel
from agentcoop.ir.dossier import (
    EvaluatorAvailability,
    Invariant,
    ResourceLimits,
    RiskLevel,
    Subgoal,
    TaskEvidenceDossier,
)
from agentcoop.ir.faults import FaultClass

# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------

#: Source-namespace identifiers, and their canonical-namespace equivalents.
#: 'S4' has no mapping, which is what makes the adapter step lossy.
MAPPING = {"S1": "C1", "S2": "C2", "S3": "C3"}
SOURCE_IDS = ["S1", "S2", "S3", "S4"]

#: What the analysis step knows, keyed by canonical identifier.
FINDINGS = {
    "C1": ["F-alpha"],
    "C2": ["F-beta"],
    "C3": ["F-gamma"],
    #: Reachable only from the source namespace directly — this is what makes
    #: the mapping detour genuinely harmful in the third regime.
    "S4": ["F-delta"],
}

#: The same analysis, but able to read source identifiers directly.
DIRECT_FINDINGS = {sid: FINDINGS.get(MAPPING.get(sid, sid), []) for sid in SOURCE_IDS}


def _type(
    name: str,
    key: str,
    *,
    facets: Optional[dict[str, str]] = None,
    required_facets: Optional[list[str]] = None,
) -> ArtifactType:
    return ArtifactType(
        name=name,
        json_schema={
            "type": "object",
            "required": [key],
            "properties": {key: {"type": "array"}, "n": {"type": "integer"}},
        },
        required_facets=required_facets or ["namespace"],
        facets=facets or {},
    )


SOURCE_SET = _type("source_set", "items", facets={"namespace": "source"})
CANONICAL_SET = _type("canonical_set", "items", facets={"namespace": "canonical"})
FINDING_SET = _type("finding_set", "results", facets={"namespace": "finding"})
REPORT = _type("report", "summary", facets={"namespace": "report"})

ALL_TYPES = [SOURCE_SET, CANONICAL_SET, FINDING_SET, REPORT]


def source_artifact(items: Optional[list[str]] = None) -> Artifact:
    payload = {"items": list(items if items is not None else SOURCE_IDS)}
    payload["n"] = len(payload["items"])
    return make_artifact(
        type_name="source_set",
        payload=payload,
        facets={"namespace": "source"},
        producer="<input>",
    )


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def _card(
    name: str,
    kind: ComponentKind,
    capabilities: list[str],
    consumes: list[ArtifactType],
    produces: list[ArtifactType],
    *,
    description: str = "",
    cost: Optional[CostProfile] = None,
    deterministic: bool = True,
    binding: Optional[dict[str, Any]] = None,
) -> CapabilityCard:
    return CapabilityCard(
        name=name,
        kind=kind,
        description=description,
        functional_capabilities=list(capabilities),
        io=IOContract(consumes=list(consumes), produces=list(produces)),
        behavior=BehaviorContract(
            deterministic=deterministic, idempotent=deterministic, shadow_safe=True
        ),
        declared_cost=cost or CostProfile(usd=0.01),
        binding=dict(binding or {}),
    )


def mapper_spec() -> ComponentSpec:
    """Lossy identifier mapping. Refuses input in the wrong namespace."""
    return ComponentSpec(
        card=_card(
            "mapper",
            ComponentKind.ADAPTER,
            ["map_identifiers"],
            [SOURCE_SET],
            [CANONICAL_SET],
            description="maps source identifiers to canonical ones; unmapped ids are dropped",
            cost=CostProfile(usd=0.01),
        ),
        behavior="map_namespace",
        params={
            "output_type": "canonical_set",
            "input_type": "source_set",
            "key": "items",
            "table": MAPPING,
            "strict": True,
            "requires_facets": {"namespace": "source"},
            "emits": {"namespace": "canonical"},
        },
    )


def analyst_spec() -> ComponentSpec:
    """Looks findings up by canonical identifier only."""
    return ComponentSpec(
        card=_card(
            "analyst",
            ComponentKind.EXTERNAL_REPO,
            ["find_associations"],
            [CANONICAL_SET],
            [FINDING_SET],
            description="canonical-namespace association lookup",
            cost=CostProfile(usd=0.02),
        ),
        behavior="lookup",
        params={
            "output_type": "finding_set",
            "input_type": "canonical_set",
            "key": "items",
            "output_key": "results",
            "table": FINDINGS,
            "strict": True,
            "requires_facets": {"namespace": "canonical"},
            "emits": {"namespace": "finding"},
        },
    )


def direct_analyst_spec() -> ComponentSpec:
    """Reads the source namespace directly, so no mapping detour is needed."""
    return ComponentSpec(
        card=_card(
            "direct_analyst",
            ComponentKind.EXTERNAL_REPO,
            ["find_associations"],
            [SOURCE_SET],
            [FINDING_SET],
            description="association lookup that understands source identifiers natively",
            cost=CostProfile(usd=0.02),
        ),
        behavior="lookup",
        params={
            "output_type": "finding_set",
            "input_type": "source_set",
            "key": "items",
            "output_key": "results",
            "table": DIRECT_FINDINGS,
            "strict": True,
            "requires_facets": {"namespace": "source"},
            "emits": {"namespace": "finding"},
        },
    )


def reporter_spec() -> ComponentSpec:
    return ComponentSpec(
        card=_card(
            "reporter",
            ComponentKind.LLM,
            ["summarize"],
            [FINDING_SET],
            [REPORT],
            description="summarizes findings into a report",
            cost=CostProfile(usd=0.005),
        ),
        behavior="reduce",
        params={
            "output_type": "report",
            "output_key": "summary",
            "emits": {"namespace": "report"},
        },
    )


def generalist_spec(
    *,
    name: str = "generalist",
    quality: Optional[dict[str, float]] = None,
    routes: Optional[dict[str, dict[str, Any]]] = None,
    capabilities: Optional[list[str]] = None,
    consumes: Optional[list[ArtifactType]] = None,
    produces: Optional[list[ArtifactType]] = None,
) -> ComponentSpec:
    """One component that claims every capability.

    Its per-subgoal ``quality`` is what decides whether a single-agent
    baseline is adequate for a task, so it is a parameter rather than a
    constant. Rigging it low everywhere would manufacture the paper's result.
    """
    default_routes: dict[str, dict[str, Any]] = {
        "map": {
            "behavior": "map_namespace",
            "output_type": "canonical_set",
            "input_type": "source_set",
            "key": "items",
            "table": MAPPING,
            "strict": True,
            "requires_facets": {"namespace": "source"},
            "emits": {"namespace": "canonical"},
        },
        "analyze": {
            "behavior": "lookup",
            "output_type": "finding_set",
            "input_type": "canonical_set",
            "key": "items",
            "output_key": "results",
            "table": FINDINGS,
            "strict": True,
            "requires_facets": {"namespace": "canonical"},
            "emits": {"namespace": "finding"},
        },
        "analyze_direct": {
            "behavior": "lookup",
            "output_type": "finding_set",
            "input_type": "source_set",
            "key": "items",
            "output_key": "results",
            "table": DIRECT_FINDINGS,
            "strict": True,
            "requires_facets": {"namespace": "source"},
            "emits": {"namespace": "finding"},
        },
        "report": {
            "behavior": "reduce",
            "output_type": "report",
            "output_key": "summary",
            "emits": {"namespace": "report"},
        },
        # Fallback route, used by probes (which carry no subgoal) and by any
        # subgoal the suite did not name. Strict, so the generalist can still
        # earn its certification.
        "*": {
            "behavior": "lookup",
            "output_type": "finding_set",
            "key": "items",
            "output_key": "results",
            "table": DIRECT_FINDINGS,
            "strict": True,
            "requires_facets": {"namespace": "source"},
            "emits": {"namespace": "finding"},
        },
    }
    return ComponentSpec(
        card=_card(
            name,
            ComponentKind.LLM,
            capabilities or ["map_identifiers", "find_associations", "summarize"],
            consumes or [SOURCE_SET, CANONICAL_SET, FINDING_SET],
            produces or [CANONICAL_SET, FINDING_SET, REPORT],
            description="a strong general model that claims every capability",
            cost=CostProfile(usd=0.03),
            # How to elicit each declared output. Without it the probe layer
            # only ever sees whichever artifact a default invocation happens
            # to emit, and a multi-capability card would sit at REACHABLE for
            # a reason unrelated to its quality.
            binding={
                "probe_contexts": {
                    "canonical_set": {"subgoal_id": "map"},
                    "finding_set": {"subgoal_id": "analyze_direct"},
                    "report": {"subgoal_id": "report"},
                }
            },
        ),
        behavior="generalist",
        params={"routes": routes or default_routes, "quality": quality or {}},
    )


# ---------------------------------------------------------------------------
# Dossiers
# ---------------------------------------------------------------------------


def _dossier(
    task_id: str,
    goal: str,
    subgoals: list[Subgoal],
    *,
    required_outputs: list[str],
    invariants: Optional[list[Invariant]] = None,
    long_horizon: bool = False,
) -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id=task_id,
        goal=goal,
        subgoals=subgoals,
        invariants=invariants or [],
        required_outputs=required_outputs,
        provided_inputs=["source_set"],
        artifact_types=list(ALL_TYPES),
        evaluators={
            CheckLevel.HARD: EvaluatorAvailability.DETERMINISTIC,
            CheckLevel.ARTIFACT: EvaluatorAvailability.DETERMINISTIC,
            CheckLevel.PROCESS: EvaluatorAvailability.PARTIAL,
            # An open-ended report has no claim-level oracle. Saying so is the
            # point: the harness must not invent a proxy score for it.
            CheckLevel.CLAIM: EvaluatorAvailability.UNAVAILABLE,
            CheckLevel.PREFERENCE: EvaluatorAvailability.UNAVAILABLE,
            CheckLevel.RESOURCE: EvaluatorAvailability.DETERMINISTIC,
        },
        limits=ResourceLimits(max_usd=1.0, max_component_calls=12),
        risk=RiskLevel.LOW,
        long_horizon=long_horizon,
    )


def _subgoal(
    subgoal_id: str,
    capability: str,
    consumes: list[str],
    produces: list[str],
    *,
    facets: Optional[dict[str, dict[str, str]]] = None,
    min_certification: str = "PROBED",
    depends_on: Optional[list[str]] = None,
) -> Subgoal:
    return Subgoal(
        subgoal_id=subgoal_id,
        description=f"{capability} on {', '.join(consumes) or 'the provided input'}",
        required_capability=capability,
        consumes=consumes,
        produces=produces,
        required_output_facets=facets or {},
        min_certification=min_certification,
        depends_on=depends_on or [],
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def task_single_sufficient() -> BenchTask:
    """One component covers everything. Composing is legal but pointless."""
    dossier = _dossier(
        "syn-single",
        "produce a finding set from the provided source identifiers",
        [
            _subgoal(
                "analyze_direct",
                "find_associations",
                ["source_set"],
                ["finding_set"],
                facets={"finding_set": {"namespace": "finding"}},
            )
        ],
        required_outputs=["finding_set"],
    )
    expected = sorted({f for sid in SOURCE_IDS for f in DIRECT_FINDINGS.get(sid, [])})
    return BenchTask(
        task_id="syn-single",
        description="a task one certified component fully discharges",
        dossier=dossier,
        component_specs=[direct_analyst_spec(), generalist_spec(), reporter_spec()],
        inputs={"source_set": source_artifact()},
        ground_truth=GroundTruth(
            expected_outputs={"finding_set": {"results": expected, "n": len(expected)}},
            minimal_component_set=["direct_analyst"],
            irreducible_subgoals=[],
            notes=["a second component cannot improve this result"],
        ),
        regime="single_sufficient",
        horizon="short",
    )


def task_multi_necessary() -> BenchTask:
    """No single component can produce the answer.

    The analyst refuses source identifiers, and the generalist is weak at the
    analysis subgoal, so the mapping step is not an optional nicety.
    """
    dossier = _dossier(
        "syn-multi",
        "map the source identifiers, find associations, and report",
        [
            _subgoal(
                "map",
                "map_identifiers",
                ["source_set"],
                ["canonical_set"],
                facets={"canonical_set": {"namespace": "canonical"}},
            ),
            _subgoal(
                "analyze",
                "find_associations",
                ["canonical_set"],
                ["finding_set"],
                facets={"finding_set": {"namespace": "finding"}},
            ),
            _subgoal("report", "summarize", ["finding_set"], ["report"]),
        ],
        required_outputs=["report"],
        invariants=[
            Invariant(
                invariant_id="namespace_consistency",
                description="associations must be looked up in the canonical namespace",
                check="edge_type_compatibility",
            )
        ],
    )
    return BenchTask(
        task_id="syn-multi",
        description="a task that cannot be discharged without composition",
        dossier=dossier,
        component_specs=[
            mapper_spec(),
            analyst_spec(),
            reporter_spec(),
            generalist_spec(quality={"analyze": 0.34}),
        ],
        inputs={"source_set": source_artifact()},
        ground_truth=GroundTruth(
            expected_outputs={},
            minimal_component_set=["mapper", "analyst", "reporter"],
            irreducible_subgoals=["map", "analyze"],
            has_scalar_oracle=False,
            notes=[
                "no scalar oracle: the report is judged by the evaluation contract "
                "stack, not by string equality"
            ],
        ),
        regime="multi_necessary",
        horizon="medium",
    )


def task_multi_harmful() -> BenchTask:
    """Composition is available, and strictly worse.

    Routing through the mapper drops 'S4', whose finding the direct analyst
    would have found. A system that composes here produces a plausible,
    incomplete answer — the exact failure the method claims to avoid.
    """
    dossier = _dossier(
        "syn-harmful",
        "find every association for the provided source identifiers",
        [
            _subgoal(
                "analyze_direct",
                "find_associations",
                ["source_set"],
                ["finding_set"],
                facets={"finding_set": {"namespace": "finding"}},
            )
        ],
        required_outputs=["finding_set"],
    )
    expected = sorted({f for sid in SOURCE_IDS for f in DIRECT_FINDINGS.get(sid, [])})
    return BenchTask(
        task_id="syn-harmful",
        description="composition is possible here and loses data",
        dossier=dossier,
        component_specs=[
            direct_analyst_spec(),
            mapper_spec(),
            analyst_spec(),
            generalist_spec(),
        ],
        inputs={"source_set": source_artifact()},
        ground_truth=GroundTruth(
            expected_outputs={"finding_set": {"results": expected, "n": len(expected)}},
            minimal_component_set=["direct_analyst"],
            irreducible_subgoals=[],
            notes=[
                "the mapper drops S4, so any workflow routing through it returns "
                "an incomplete finding set that still looks well-formed"
            ],
        ),
        regime="multi_harmful",
        horizon="short",
    )


def task_silent_corruption() -> BenchTask:
    """The same composition task, with a silent namespace corruption planted."""
    task = task_multi_necessary()
    return task.model_copy(
        update={
            "task_id": "syn-silent-namespace",
            "description": "composition with a silently corrupted handoff",
            "dossier": task.dossier.model_copy(update={"task_id": "syn-silent-namespace"}),
            "injected_faults": [
                InjectedFault(
                    fault_id="f-namespace",
                    fault_class=FaultClass.ARTIFACT_CONTRACT,
                    target="mapper",
                    mechanism=Mechanism.NAMESPACE_CORRUPTION.value,
                    expected_blame="map__mapper",
                    params={"facet": "namespace", "value": "source"},
                    silent=True,
                    description=(
                        "the mapper stamps the wrong namespace on its output; the "
                        "schema still validates and the run looks healthy"
                    ),
                )
            ],
        },
        deep=True,
    )


def task_silent_empty() -> BenchTask:
    """Exit code zero, empty result — the failure that gets believed."""
    task = task_multi_necessary()
    return task.model_copy(
        update={
            "task_id": "syn-silent-empty",
            "description": "composition where one node succeeds with nothing in it",
            "dossier": task.dossier.model_copy(update={"task_id": "syn-silent-empty"}),
            "injected_faults": [
                InjectedFault(
                    fault_id="f-empty",
                    fault_class=FaultClass.ARTIFACT_CONTRACT,
                    target="analyst",
                    mechanism=Mechanism.SILENT_EMPTY_OUTPUT.value,
                    expected_blame="analyze__analyst",
                    silent=True,
                    description="the analyst returns an empty finding set with exit code 0",
                )
            ],
        },
        deep=True,
    )


def task_dependency_break() -> BenchTask:
    """A loud fault, for contrast: the environment is simply missing."""
    task = task_multi_necessary()
    return task.model_copy(
        update={
            "task_id": "syn-dependency-break",
            "description": "composition where one component's runtime is unavailable",
            "dossier": task.dossier.model_copy(update={"task_id": "syn-dependency-break"}),
            "injected_faults": [
                InjectedFault(
                    fault_id="f-dependency",
                    fault_class=FaultClass.ENVIRONMENT,
                    target="analyst",
                    mechanism=Mechanism.DEPENDENCY_BREAK.value,
                    expected_blame="analyze__analyst",
                    params={"package": "libassoc"},
                    description="the analyst cannot start at all",
                )
            ],
        },
        deep=True,
    )


def task_needless_serialization() -> BenchTask:
    """A coordination fault that no node-level repair can address."""
    dossier = _dossier(
        "syn-serialized",
        "run two independent analyses and report both",
        [
            _subgoal(
                "analyze_direct",
                "find_associations",
                ["source_set"],
                ["finding_set"],
                facets={"finding_set": {"namespace": "finding"}},
            ),
            _subgoal(
                "map",
                "map_identifiers",
                ["source_set"],
                ["canonical_set"],
                facets={"canonical_set": {"namespace": "canonical"}},
            ),
        ],
        required_outputs=["finding_set", "canonical_set"],
    )
    return BenchTask(
        task_id="syn-serialized",
        description="two independent subgoals, artificially chained",
        dossier=dossier,
        component_specs=[direct_analyst_spec(), mapper_spec(), generalist_spec()],
        inputs={"source_set": source_artifact()},
        ground_truth=GroundTruth(
            expected_outputs={},
            minimal_component_set=["direct_analyst", "mapper"],
            irreducible_subgoals=["analyze_direct", "map"],
            has_scalar_oracle=False,
        ),
        injected_faults=[
            InjectedFault(
                fault_id="f-serialization",
                fault_class=FaultClass.COORDINATION,
                target="map",
                mechanism=Mechanism.NEEDLESS_SERIALIZATION.value,
                #: Node ids are '<subgoal>__<component>'; the blame belongs to
                #: the node made to wait, or to the edge that made it wait.
                expected_blame="map__mapper",
                params={"first": "analyze_direct", "second": "map"},
                description=(
                    "'map' is forced to wait on an analysis it does not consume"
                ),
            )
        ],
        regime="multi_necessary",
        horizon="medium",
    )


def synthetic_suite() -> BenchSuite:
    """The full suite. Construction only — nothing here executes."""
    return BenchSuite(
        suite_id="synthetic-v1",
        description=(
            "Three-regime synthetic suite with silent and loud injected faults. "
            "Every ground truth is knowable by construction."
        ),
        tasks=[
            task_single_sufficient(),
            task_multi_necessary(),
            task_multi_harmful(),
            task_silent_corruption(),
            task_silent_empty(),
            task_dependency_break(),
            task_needless_serialization(),
        ],
    )


__all__ = [
    "MAPPING",
    "SOURCE_IDS",
    "FINDINGS",
    "DIRECT_FINDINGS",
    "SOURCE_SET",
    "CANONICAL_SET",
    "FINDING_SET",
    "REPORT",
    "ALL_TYPES",
    "source_artifact",
    "mapper_spec",
    "analyst_spec",
    "direct_analyst_spec",
    "reporter_spec",
    "generalist_spec",
    "task_single_sufficient",
    "task_multi_necessary",
    "task_multi_harmful",
    "task_silent_corruption",
    "task_silent_empty",
    "task_dependency_break",
    "task_needless_serialization",
    "synthetic_suite",
]
