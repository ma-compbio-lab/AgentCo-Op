"""Probe specifications, and the synthetic payloads they run on.

A probe is a *falsifiable question* put to a component by executing it. The
six kinds are ordered by what they can establish:

``reachable``       the entrypoint exists and can be invoked at all
``schema``          it emits the outputs it declares, structurally valid
``smoke``           a realistic input produces a non-vacuous result
``invalid_input``   it *refuses* malformed input instead of inventing a result
``determinism``     the same seed and inputs give the same content hashes
``resource``        it stays inside a declared budget, or fails cleanly

The first three are positive: they can only ever confirm that something works
on the happy path. The last three are the ones that carry weight, because a
component that passes only the positive probes is exactly the component that
will later return an empty gene set with exit code 0 and be believed.

Payload synthesis lives here rather than in the runner so that probe inputs
are a pure function of the declared contract. That matters twice over: the
same card always produces the same probe suite (probe ids are content-derived,
so a certification is reproducible), and the *only* thing a probe can be
accused of testing is the contract the component published.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import Artifact, ArtifactType, FacetSet

ProbeKind = Literal[
    "reachable", "schema", "smoke", "invalid_input", "determinism", "resource"
]

#: Every kind, in ladder order. Iterating this rather than a set keeps probe
#: ordering — and therefore probe ids — stable across runs.
PROBE_KINDS: tuple[ProbeKind, ...] = (
    "reachable",
    "schema",
    "smoke",
    "invalid_input",
    "determinism",
    "resource",
)


class Corruption(str, Enum):
    """How an ``invalid_input`` probe breaks its payload.

    Each corruption targets a different way a component can be wrong about
    its input, and they are *not* interchangeable. Dropping a required field
    tests whether the component validates structure; swapping a facet tests
    whether it validates meaning. A tool that checks the former and not the
    latter is the one that maps mouse genes against a human background and
    reports a confident, wrong enrichment.
    """

    #: Remove a property the type declares as required.
    MISSING_REQUIRED = "missing_required"
    #: Keep the shape, break the types (string where an integer belongs).
    WRONG_TYPE = "wrong_type"
    #: Structurally perfect, semantically nonsense — the facets lie.
    FACET_VIOLATION = "facet_violation"
    #: Nothing at all. The degenerate input every component should refuse.
    EMPTY = "empty"


class ProbeSpec(BaseModel):
    """One executable question about a component.

    ``config`` and ``seed`` shape the *invocation*; ``expectations`` records
    what the runner will check. Keeping them apart means a probe's verdict is
    never a function of how it happened to be dispatched.
    """

    model_config = ConfigDict(extra="forbid")

    probe_id: str
    kind: ProbeKind
    description: str
    inputs: dict[str, Artifact] = Field(default_factory=dict)
    expectations: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 60.0
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int = 0
    #: Role this probe invokes the component in. A component that routes on
    #: subgoal — any generalist does — cannot demonstrate more than one of its
    #: declared outputs without it, and would otherwise be stuck at REACHABLE
    #: for the sole reason that a single invocation emits a single artifact.
    subgoal_id: Optional[str] = None

    @property
    def is_negative(self) -> bool:
        """Negative probes assert a *refusal*; passing means the component said no."""
        return self.kind in ("invalid_input", "resource")


# ---------------------------------------------------------------------------
# Deterministic payload synthesis
# ---------------------------------------------------------------------------

#: Values used to fill a declared schema. Chosen to be obviously synthetic so
#: that a probe payload showing up in a real result is recognisable as such.
_DEFAULTS: dict[str, Any] = {
    "string": "probe",
    "integer": 1,
    "number": 1.0,
    "boolean": True,
    "array": None,   # handled via `items`
    "object": None,  # handled via nested `properties`
    "null": None,
}

#: Type used when corrupting a field, per declared type. Always something a
#: correct validator must reject.
_WRONG: dict[str, Any] = {
    "string": 12345,
    "integer": "not-an-integer",
    "number": "not-a-number",
    "boolean": "not-a-boolean",
    "array": {"not": "an array"},
    "object": ["not an object"],
}


def synthesize_payload(artifact_type: ArtifactType, *, depth: int = 0) -> Any:
    """Build a minimal payload that satisfies ``artifact_type.json_schema``.

    Only the subset the registry actually validates is honoured — ``required``
    and ``properties[*].type`` — because synthesizing against schema features
    the validator ignores would produce probes that fail for reasons the
    system cannot subsequently explain.
    """
    schema = artifact_type.json_schema or {}
    if not schema:
        return {"probe": True}
    return _synthesize_from_schema(schema, depth=depth)


def _synthesize_from_schema(schema: dict[str, Any], *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    declared = schema.get("type")

    if declared == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return [_synthesize_from_schema(items, depth=depth + 1)]
        return ["probe"]

    if declared in _DEFAULTS and declared not in ("object", "array"):
        return _DEFAULTS[declared]

    properties = schema.get("properties") or {}
    if declared != "object" and not properties:
        return {"probe": True}

    payload: dict[str, Any] = {}
    # Every declared property, not just the required ones: a schema probe that
    # exercised only the required subset would never notice a component that
    # drops optional-but-declared fields.
    for key in sorted(properties):
        spec = properties[key]
        if isinstance(spec, dict):
            payload[key] = _synthesize_from_schema(spec, depth=depth + 1)
        else:
            payload[key] = "probe"
    for key in schema.get("required") or []:
        payload.setdefault(key, "probe")
    return payload


def corrupt_payload(
    payload: Any, artifact_type: ArtifactType, corruption: Corruption
) -> tuple[Any, FacetSet, str]:
    """Return ``(payload, facet_overrides, human_description)`` for a corruption.

    Returns the description too, because when this probe fails the message the
    user reads is "component accepted <this specific defect>" — a generic
    "invalid input was accepted" would leave them unable to tell a missing
    field from a wrong organism.
    """
    schema = artifact_type.json_schema or {}
    properties = schema.get("properties") or {}
    required = list(schema.get("required") or [])

    if corruption is Corruption.EMPTY:
        return ({} if isinstance(payload, dict) else None), {}, "an empty payload"

    if corruption is Corruption.MISSING_REQUIRED:
        if not isinstance(payload, dict) or not required:
            return None, {}, "a null payload where content was required"
        victim = sorted(required)[0]
        stripped = {k: v for k, v in payload.items() if k != victim}
        return stripped, {}, f"a payload missing the required property '{victim}'"

    if corruption is Corruption.WRONG_TYPE:
        if not isinstance(payload, dict) or not properties:
            return "a string where structured input was required", {}, (
                "a string where structured input was required"
            )
        for key in sorted(properties):
            spec = properties[key]
            declared = spec.get("type") if isinstance(spec, dict) else None
            if declared in _WRONG:
                broken = dict(payload)
                broken[key] = _WRONG[declared]
                return broken, {}, (
                    f"a payload whose '{key}' is {type(_WRONG[declared]).__name__} "
                    f"instead of {declared}"
                )
        return payload, {}, "a payload with no type-checkable property"

    # FACET_VIOLATION: the payload is perfectly well-formed and the *meaning*
    # is wrong. This is the corruption a schema validator cannot catch.
    overrides = {key: f"invalid_{key}" for key in artifact_type.required_facets}
    if not overrides:
        overrides = {"namespace": "invalid_namespace"}
    described = ", ".join(f"{k}={v}" for k, v in sorted(overrides.items()))
    return payload, overrides, f"a well-formed payload with impossible facets ({described})"


def probe_artifact(
    artifact_type: ArtifactType,
    payload: Any,
    *,
    facet_overrides: Optional[FacetSet] = None,
    producer: str = "probe",
) -> Artifact:
    """A fully-identified artifact carrying a synthesized payload."""
    facets: FacetSet = {**artifact_type.facets, **(facet_overrides or {})}
    art = Artifact(
        artifact_id="",
        type_name=artifact_type.name,
        facets=facets,
        payload=payload,
        producer=producer,
        notes=["synthetic_probe_input"],
    ).finalize()
    art.artifact_id = f"{producer}::{artifact_type.name}::{art.content_hash}"
    return art


def probe_id_for(component: str, kind: str, *parts: str) -> str:
    """Content-derived probe id.

    Derived from the component, kind and discriminating parts rather than a
    counter, so re-running ``standard_suite`` on an unchanged card yields
    identical ids and two certifications are diffable.
    """
    blob = "\x1f".join((component, kind, *parts)).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:8]
    return f"{component}:{kind}:{digest}"


def canonical(value: Any) -> str:
    """Stable serialization used when comparing determinism-probe outputs."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


__all__ = [
    "ProbeKind",
    "PROBE_KINDS",
    "Corruption",
    "ProbeSpec",
    "synthesize_payload",
    "corrupt_payload",
    "probe_artifact",
    "probe_id_for",
    "canonical",
]
