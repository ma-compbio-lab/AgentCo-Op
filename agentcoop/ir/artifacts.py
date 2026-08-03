"""Typed artifacts with semantic facets and provenance lineage.

The single most common way heterogeneous scientific components fail to
compose is *not* a JSON schema mismatch — both sides happily agree on
``{"genes": ["..."]}``. They fail because one side emits Ensembl IDs and the
other expects HGNC symbols, or one emits mouse genes and the other assumes
human, or one emits log-normalized counts and the other assumes raw.

So an artifact type here is a pair: a **structural** schema plus a set of
**semantic facets**. Edge compatibility is decided over both. When a facet a
consumer requires is not declared by the producer, the result is
``UNDERSPECIFIED`` — explicitly *not* "compatible". That distinction is what
stops the compiler from wiring two components together on the strength of an
LLM's guess.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field


#: A semantic facet map, e.g. ``{"namespace": "HGNC", "organism": "human"}``.
FacetSet = dict[str, str]


class Compatibility(str, Enum):
    """Result of comparing a producer contract against a consumer contract."""

    COMPATIBLE = "compatible"
    #: Structurally and semantically mismatched, but a registered converter
    #: closes the gap. The compiler may insert an adapter node — and must
    #: record a design-evidence record justifying it.
    NEEDS_ADAPTER = "needs_adapter"
    #: The consumer requires a facet the producer never declares. We cannot
    #: prove compatibility, so we refuse to assume it.
    UNDERSPECIFIED = "underspecified"
    INCOMPATIBLE = "incompatible"


class CompatibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Compatibility
    producer_type: str
    consumer_type: str
    #: Facets present on both sides but with conflicting values.
    conflicting_facets: dict[str, tuple[str, str]] = Field(default_factory=dict)
    #: Facets the consumer requires that the producer does not declare.
    missing_facets: list[str] = Field(default_factory=list)
    #: Structural schema problems (missing required properties, type clashes).
    schema_problems: list[str] = Field(default_factory=list)
    #: Name of a registered converter that would resolve the mismatch.
    adapter: Optional[str] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is Compatibility.COMPATIBLE


class ArtifactType(BaseModel):
    """A named, structurally and semantically typed artifact contract."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    #: JSON-Schema-ish structural description. Only the subset we validate
    #: (``required``, ``properties[*].type``) is enforced; the rest is
    #: documentation carried through to the manifest.
    json_schema: dict[str, Any] = Field(default_factory=dict)
    #: Facet keys a *consumer* of this type must have pinned down in order to
    #: interpret the payload correctly.
    required_facets: list[str] = Field(default_factory=list)
    #: Concrete facet values asserted by whichever side declares this type.
    facets: FacetSet = Field(default_factory=dict)
    #: Whether the payload lives on disk rather than in memory.
    is_path: bool = False

    def with_facets(self, **facets: str) -> "ArtifactType":
        merged = {**self.facets, **{k: str(v) for k, v in facets.items()}}
        return self.model_copy(update={"facets": merged})


class Artifact(BaseModel):
    """A concrete artifact instance produced during execution.

    ``derived_from`` is what makes artifact-level causal tracing possible:
    diagnosis walks this lineage backwards to find the earliest artifact that
    fails its own validity check, rather than blaming whichever node happened
    to raise.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    type_name: str
    facets: FacetSet = Field(default_factory=dict)
    payload: Any = None
    path: Optional[str] = None
    content_hash: str = ""
    #: Node that emitted this artifact.
    producer: str = ""
    #: Artifact ids consumed to produce this one. The lineage DAG.
    derived_from: list[str] = Field(default_factory=list)
    step: int = 0
    #: Free-form notes carried into the run manifest.
    notes: list[str] = Field(default_factory=list)

    def compute_hash(self) -> str:
        try:
            blob = json.dumps(
                {"type": self.type_name, "facets": self.facets, "payload": self.payload},
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        except (TypeError, ValueError):
            blob = f"{self.type_name}{self.facets}{self.payload}".encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def finalize(self) -> "Artifact":
        if not self.content_hash:
            self.content_hash = self.compute_hash()
        return self


# ---------------------------------------------------------------------------
# Type registry + converters
# ---------------------------------------------------------------------------


#: A converter takes an artifact and returns a new one with different facets.
ConverterFn = Callable[[Artifact], Artifact]


class Converter(BaseModel):
    """A registered, *executable* facet conversion.

    Converters are how the compiler is allowed to bridge a facet mismatch.
    Crucially the converter must exist as code — the compiler cannot paper
    over an Ensembl/HGNC mismatch by writing "the agent will handle it".
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    type_name: str
    from_facets: FacetSet
    to_facets: FacetSet
    #: Expected fraction of entries that survive conversion; below this the
    #: static analyser flags the edge as lossy and demands a coverage check.
    expected_coverage: float = 1.0
    fn: Optional[ConverterFn] = Field(default=None, exclude=True)

    def matches(self, have: FacetSet, want: FacetSet) -> bool:
        for k, v in self.from_facets.items():
            if have.get(k) != v:
                return False
        for k, v in self.to_facets.items():
            if want.get(k) not in (None, v):
                return False
        return True


class TypeRegistry:
    """Registry of artifact types and facet converters.

    Deliberately a plain object rather than a global: tests and probe runs
    build isolated registries so certification never leaks between runs.
    """

    def __init__(self) -> None:
        self._types: dict[str, ArtifactType] = {}
        self._converters: list[Converter] = []

    # -- types --------------------------------------------------------------

    def register_type(self, artifact_type: ArtifactType) -> ArtifactType:
        self._types[artifact_type.name] = artifact_type
        return artifact_type

    def get(self, name: str) -> Optional[ArtifactType]:
        return self._types.get(name)

    def require(self, name: str) -> ArtifactType:
        t = self._types.get(name)
        if t is None:
            raise KeyError(f"artifact type '{name}' is not registered")
        return t

    def names(self) -> list[str]:
        return sorted(self._types)

    # -- converters ---------------------------------------------------------

    def register_converter(self, converter: Converter) -> Converter:
        self._converters.append(converter)
        return converter

    def find_converter(
        self, type_name: str, have: FacetSet, want: FacetSet
    ) -> Optional[Converter]:
        for c in self._converters:
            if c.type_name != type_name:
                continue
            if c.matches(have, want):
                return c
        return None

    # -- compatibility ------------------------------------------------------

    def check_compatibility(
        self,
        producer: ArtifactType,
        consumer: ArtifactType,
        *,
        allow_adapter: bool = True,
    ) -> CompatibilityReport:
        """Decide whether ``producer``'s output can feed ``consumer``'s input.

        The order of judgments matters: a hard structural clash is reported
        as incompatible even if facets happen to line up, and a missing facet
        is reported as underspecified rather than optimistically compatible.
        """
        report = CompatibilityReport(
            verdict=Compatibility.COMPATIBLE,
            producer_type=producer.name,
            consumer_type=consumer.name,
        )

        if producer.name != consumer.name:
            report.verdict = Compatibility.INCOMPATIBLE
            report.detail = (
                f"artifact type mismatch: producer emits '{producer.name}', "
                f"consumer expects '{consumer.name}'"
            )
            return report

        report.schema_problems = _schema_problems(producer.json_schema, consumer.json_schema)

        for key in consumer.required_facets:
            have = producer.facets.get(key)
            want = consumer.facets.get(key)
            if have is None:
                report.missing_facets.append(key)
            elif want is not None and have != want:
                report.conflicting_facets[key] = (have, want)

        if report.schema_problems:
            report.verdict = Compatibility.INCOMPATIBLE
            report.detail = "; ".join(report.schema_problems)
            return report

        if report.conflicting_facets:
            converter = (
                self.find_converter(producer.name, producer.facets, consumer.facets)
                if allow_adapter
                else None
            )
            if converter is not None:
                report.verdict = Compatibility.NEEDS_ADAPTER
                report.adapter = converter.name
                report.detail = (
                    "facet conflict resolvable by converter "
                    f"'{converter.name}': "
                    + ", ".join(
                        f"{k}: {a} -> {b}" for k, (a, b) in report.conflicting_facets.items()
                    )
                )
            else:
                report.verdict = Compatibility.INCOMPATIBLE
                report.detail = "unresolvable facet conflict: " + ", ".join(
                    f"{k}: producer={a}, consumer={b}"
                    for k, (a, b) in report.conflicting_facets.items()
                )
            return report

        if report.missing_facets:
            report.verdict = Compatibility.UNDERSPECIFIED
            report.detail = (
                "producer does not declare facets required by consumer: "
                + ", ".join(sorted(report.missing_facets))
                + " (probe the component or declare the facet; do not assume)"
            )
            return report

        return report


def _schema_problems(producer: dict[str, Any], consumer: dict[str, Any]) -> list[str]:
    """Structural subset check: does the producer guarantee what the consumer needs?"""
    problems: list[str] = []
    if not consumer:
        return problems
    prod_props = (producer or {}).get("properties", {}) or {}
    cons_props = consumer.get("properties", {}) or {}
    required = consumer.get("required") or []

    for key in required:
        if not prod_props:
            # Producer declares nothing structural; we cannot verify the
            # guarantee. Treated as a schema problem so it surfaces at compile
            # time instead of at run time.
            problems.append(f"producer declares no output properties, consumer requires '{key}'")
            continue
        if key not in prod_props:
            problems.append(f"consumer requires property '{key}' the producer does not emit")

    for key, spec in cons_props.items():
        want = spec.get("type") if isinstance(spec, dict) else None
        have_spec = prod_props.get(key)
        have = have_spec.get("type") if isinstance(have_spec, dict) else None
        if want and have and want != have:
            problems.append(f"property '{key}': producer emits {have}, consumer expects {want}")
    return problems


def validate_payload(payload: Any, artifact_type: ArtifactType) -> list[str]:
    """Validate a concrete payload against a type's structural schema."""
    problems: list[str] = []
    schema = artifact_type.json_schema or {}
    if not schema:
        return problems
    if schema.get("type") == "object" and not isinstance(payload, dict):
        return [f"expected object payload for '{artifact_type.name}', got {type(payload).__name__}"]
    if not isinstance(payload, dict):
        return problems

    for key in schema.get("required", []) or []:
        if key not in payload:
            problems.append(f"missing required property '{key}'")

    type_map: dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, spec in (schema.get("properties") or {}).items():
        if key not in payload or not isinstance(spec, dict):
            continue
        expected = type_map.get(spec.get("type", ""))
        if expected is None:
            continue
        # bool is a subclass of int; keep them distinct.
        if expected is int and isinstance(payload[key], bool):
            problems.append(f"property '{key}': expected integer, got boolean")
        elif not isinstance(payload[key], expected):
            problems.append(
                f"property '{key}': expected {spec.get('type')}, "
                f"got {type(payload[key]).__name__}"
            )
    return problems


def lineage_of(artifact_id: str, artifacts: dict[str, Artifact]) -> list[str]:
    """Return ``artifact_id`` plus all transitive ancestors, nearest first.

    Cycles cannot occur in a well-formed lineage, but the guard is kept so a
    corrupted trace degrades to a truncated walk instead of hanging.
    """
    seen: set[str] = set()
    order: list[str] = []
    frontier = [artifact_id]
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        order.append(current)
        art = artifacts.get(current)
        if art is None:
            continue
        frontier.extend(a for a in art.derived_from if a not in seen)
    return order


def build_lineage_index(artifacts: Iterable[Artifact]) -> dict[str, Artifact]:
    return {a.artifact_id: a for a in artifacts}


__all__ = [
    "FacetSet",
    "Compatibility",
    "CompatibilityReport",
    "ArtifactType",
    "Artifact",
    "Converter",
    "ConverterFn",
    "TypeRegistry",
    "validate_payload",
    "lineage_of",
    "build_lineage_index",
]
