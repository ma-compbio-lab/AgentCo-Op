"""Deriving a probe suite from a declared capability card.

``standard_suite`` is a pure function from a card to the set of questions that
would have to be answered by execution before that card could be believed. It
reads the *declared* half of the card only — that is the point. The suite is
the adversary of the declaration, so it must not be derived from anything the
component has already been credited with.

Two design commitments show up as code here:

**No silent caps.** Every applicable corruption of every consumed artifact
type gets its own probe. A suite that quietly tested one input and reported
"invalid_input: passed" would licence exactly the over-claim the certification
ladder exists to prevent. A card with three inputs simply gets a longer suite.

**Zero-input components are still probed for refusal.** A component that
consumes nothing has no malformed artifact to reject, so its ``invalid_input``
probe attacks the configuration surface instead. Skipping the probe would let
source components reach CERTIFIED without ever demonstrating that they refuse
anything, which is the weakest possible reading of the word.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, NamedTuple, Optional, Sequence

from agentcoop.ir.artifacts import ArtifactType, TypeRegistry
from agentcoop.ir.capability import CapabilityCard, CostProfile
from agentcoop.probe.spec import (
    Corruption,
    ProbeSpec,
    corrupt_payload,
    probe_artifact,
    probe_id_for,
    synthesize_payload,
)

#: Wall-time budget handed to the resource probe when the card declares none.
DEFAULT_RESOURCE_LIMIT_S = 60.0

#: Seed used by the determinism probe. Fixed, because varying it would make
#: certification irreproducible for exactly the components it is testing.
DETERMINISM_SEED = 20240617


def _strict_json_value(value: Any, *, seen: set[int], depth: int = 0) -> Any:
    if depth > 64:
        raise ValueError("parameter value nesting exceeds limit")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("parameter values must be finite")
        return value
    if type(value) is str:
        value.encode("utf-8")
        return value
    if type(value) is list:
        identity = id(value)
        if identity in seen:
            raise ValueError("parameter values cannot be cyclic")
        seen.add(identity)
        try:
            return [
                _strict_json_value(item, seen=seen, depth=depth + 1)
                for item in value
            ]
        finally:
            seen.remove(identity)
    if type(value) is dict:
        identity = id(value)
        if identity in seen:
            raise ValueError("parameter values cannot be cyclic")
        if any(type(key) is not str for key in value):
            raise ValueError("parameter mappings require string keys")
        for key in value:
            key.encode("utf-8")
        seen.add(identity)
        try:
            return {
                key: _strict_json_value(value[key], seen=seen, depth=depth + 1)
                for key in sorted(value)
            }
        finally:
            seen.remove(identity)
    raise ValueError("parameter values must be canonical JSON")


def _strict_canonical_json(value: Any) -> str:
    normalized = _strict_json_value(value, seen=set())
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_parameter_values(values: Sequence[Any]) -> tuple[Any, ...]:
    normalized = [
        (_strict_canonical_json(value), _strict_json_value(value, seen=set()))
        for value in values
    ]
    if not normalized:
        raise ValueError("parameter domain values must not be empty")
    serialized = [item[0] for item in normalized]
    if len(serialized) != len(set(serialized)):
        raise ValueError("parameter domain values must be unique")
    return tuple(value for _, value in sorted(normalized, key=lambda item: item[0]))


def _parameter_values_hash(values: Sequence[Any]) -> str:
    canonical_values = _canonical_parameter_values(values)
    return _sha256(_strict_canonical_json(list(canonical_values)))


def _parameter_value_hash(value: Any) -> str:
    return _sha256(_strict_canonical_json(value))


def resolve_type(
    declared: ArtifactType, registry: Optional[TypeRegistry]
) -> ArtifactType:
    """Merge a component's declared type with the registry's canonical one.

    The registry holds the task-level truth about a type (its schema and which
    facets a consumer must have pinned down); the card holds this component's
    claims about the values. Probing against the merge catches a component
    that declares ``gene_set`` while ignoring half of what a ``gene_set`` is
    contractually required to carry.
    """
    if registry is None:
        return declared
    canonical = registry.get(declared.name)
    if canonical is None:
        return declared
    return canonical.model_copy(
        update={
            "facets": {**canonical.facets, **declared.facets},
            "required_facets": sorted(
                set(canonical.required_facets) | set(declared.required_facets)
            ),
            "json_schema": declared.json_schema or canonical.json_schema,
        }
    )


def applicable_corruptions(artifact_type: ArtifactType) -> list[Corruption]:
    """Corruptions that are meaningful for this type, in a stable order.

    A corruption that cannot actually be applied is omitted rather than
    emitted-and-passed: a probe that degenerates into "we sent valid input and
    it worked" would inflate the certification with a test that asserts
    nothing.
    """
    schema = artifact_type.json_schema or {}
    properties = schema.get("properties") or {}
    out: list[Corruption] = [Corruption.EMPTY]

    if schema.get("required"):
        out.append(Corruption.MISSING_REQUIRED)
    if any(isinstance(s, dict) and s.get("type") for s in properties.values()):
        out.append(Corruption.WRONG_TYPE)
    if artifact_type.required_facets or artifact_type.facets:
        out.append(Corruption.FACET_VIOLATION)
    return out


def _valid_inputs(
    card: CapabilityCard, registry: Optional[TypeRegistry]
) -> dict[str, Any]:
    inputs = {}
    for declared in card.io.consumes:
        resolved = resolve_type(declared, registry)
        inputs[resolved.name] = probe_artifact(
            resolved, synthesize_payload(resolved)
        )
    return inputs


class OutputContext(NamedTuple):
    """One invocation shape under which a card claims to emit something."""

    produces: list[str]
    subgoal_id: Optional[str] = None
    config: dict[str, Any] = {}

    @property
    def probe_suffix(self) -> tuple[str, ...]:
        return tuple(self.produces) if self.produces else ()

    @property
    def described(self) -> str:
        if not self.produces:
            return "no declared output"
        target = f"'{self.produces[0]}'" if len(self.produces) == 1 else (
            f"{len(self.produces)} declared outputs"
        )
        return target + (f" in role '{self.subgoal_id}'" if self.subgoal_id else "")


def _output_contexts(card: CapabilityCard) -> list[OutputContext]:
    """The invocations a probe must make to cover every declared output.

    A single-output component needs one. A component claiming several outputs
    almost never emits them all from one call — a generalist emits whichever
    the current role calls for — so probing it once would fail the schema
    probe for a component that is behaving correctly, and pin every
    multi-capability component at REACHABLE for a reason that has nothing to
    do with its quality.

    The way to elicit each output is something only the component's author
    knows, so it is declared in ``binding["probe_contexts"]``, mapping an
    artifact type name to the ``subgoal_id`` and ``config`` that produce it.
    A card that claims several outputs and declares no way to elicit them gets
    one probe per output anyway, and fails the ones it cannot demonstrate.
    That is the intended verdict: an unqualified claim with no way to check it
    is not evidence.
    """
    produced = [t.name for t in card.io.produces]
    if not produced:
        return [OutputContext(produces=[])]

    declared = card.binding.get("probe_contexts")
    routes = declared if isinstance(declared, dict) else {}

    if len(produced) == 1 and not routes:
        return [OutputContext(produces=list(produced))]

    contexts: list[OutputContext] = []
    for type_name in produced:
        route = routes.get(type_name)
        route = route if isinstance(route, dict) else {}
        contexts.append(
            OutputContext(
                produces=[type_name],
                subgoal_id=route.get("subgoal_id"),
                config=dict(route.get("config") or {}),
            )
        )
    return contexts


def _smoke_inputs(
    card: CapabilityCard,
    registry: Optional[TypeRegistry],
    valid: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Real inputs when the card supplies them, synthesized ones otherwise.

    ``binding["smoke_inputs"]`` maps artifact type name to a payload the
    component's author says is representative. It is the only place in
    certification where a declaration is trusted — and it is trusted only to
    make the probe *harder*, never to excuse a failure.
    """
    declared_payloads = card.binding.get("smoke_inputs")
    if not isinstance(declared_payloads, dict) or not declared_payloads:
        return dict(valid), "synthetic"

    inputs = dict(valid)
    used = False
    for declared in card.io.consumes:
        resolved = resolve_type(declared, registry)
        if resolved.name in declared_payloads:
            inputs[resolved.name] = probe_artifact(
                resolved, declared_payloads[resolved.name]
            )
            used = True
    return inputs, ("declared" if used else "synthetic")


def _resource_limits(card: CapabilityCard) -> dict[str, Any]:
    declared: CostProfile = card.declared_cost
    return {
        "max_wall_time_s": declared.latency_s or DEFAULT_RESOURCE_LIMIT_S,
        "max_usd": declared.usd or None,
        "max_tokens": declared.tokens or None,
    }


def standard_suite(
    card: CapabilityCard, *, registry: Optional[TypeRegistry] = None
) -> list[ProbeSpec]:
    """The full battery of probes implied by ``card``'s declarations."""
    specs: list[ProbeSpec] = []
    name = card.name
    valid = _valid_inputs(card, registry)
    produced = [t.name for t in card.io.produces]

    # -- reachable ---------------------------------------------------------
    specs.append(
        ProbeSpec(
            probe_id=probe_id_for(name, "reachable"),
            kind="reachable",
            description=f"invoke '{name}' and confirm the entrypoint exists",
            inputs=dict(valid),
            expectations={"entrypoint_resolves": True},
        )
    )

    # -- schema / smoke ----------------------------------------------------
    smoke_inputs, input_source = _smoke_inputs(card, registry, valid)
    for context in _output_contexts(card):
        suffix = context.probe_suffix
        specs.append(
            ProbeSpec(
                probe_id=probe_id_for(name, "schema", *suffix),
                kind="schema",
                description=(
                    f"confirm {context.described} is emitted and structurally valid"
                    if context.produces
                    else "confirm the component emits no undeclared outputs"
                ),
                inputs=dict(valid),
                expectations={"produces": context.produces, "validate_schema": True},
                config=dict(context.config),
                subgoal_id=context.subgoal_id,
            )
        )
        specs.append(
            ProbeSpec(
                probe_id=probe_id_for(name, "smoke", *suffix),
                kind="smoke",
                description=(
                    f"confirm a {input_source} input yields a non-vacuous "
                    f"{context.described}"
                ),
                inputs=dict(smoke_inputs),
                expectations={
                    "produces": context.produces,
                    "require_non_empty": True,
                    # Deep emptiness (``{"status":"ok","genes":[]}``) is only a
                    # defect when the input was real. On a synthesized input, an
                    # empty result set can be the correct answer, and failing the
                    # probe for it would punish honest components.
                    "input_source": input_source,
                },
                config=dict(context.config),
                subgoal_id=context.subgoal_id,
            )
        )

    # -- invalid_input -----------------------------------------------------
    specs.extend(_invalid_input_specs(card, registry, valid))

    # -- determinism -------------------------------------------------------
    specs.append(
        ProbeSpec(
            probe_id=probe_id_for(name, "determinism"),
            kind="determinism",
            description="run twice with one seed and compare artifact content hashes",
            inputs=dict(valid),
            expectations={"repeat": 2, "compare": "content_hash"},
            seed=DETERMINISM_SEED,
        )
    )

    # -- resource ----------------------------------------------------------
    specs.append(
        ProbeSpec(
            probe_id=probe_id_for(name, "resource"),
            kind="resource",
            description="confirm the component honours a stated budget or fails cleanly",
            inputs=dict(valid),
            expectations={"limits": _resource_limits(card)},
        )
    )

    return specs


def parameter_domain_suite(
    card: CapabilityCard,
    *,
    parameter: str,
    values: Sequence[Any],
    registry: Optional[TypeRegistry] = None,
) -> list[ProbeSpec]:
    """Probe a finite declared configuration domain through existing handlers."""
    if not isinstance(parameter, str) or not parameter or parameter != parameter.strip():
        raise ValueError("parameter must be a non-empty canonical string")
    if parameter not in card.io.parameters:
        raise ValueError(f"parameter '{parameter}' is not declared by '{card.name}'")
    canonical_values = _canonical_parameter_values(values)
    allowed_values_hash = _parameter_values_hash(canonical_values)
    contract_hash = _sha256(_strict_canonical_json(card.io.model_dump(mode="python")))
    valid = _valid_inputs(card, registry)
    smoke_inputs, input_source = _smoke_inputs(card, registry, valid)
    contexts = _output_contexts(card)
    specs: list[ProbeSpec] = []

    for value in canonical_values:
        value_hash = _parameter_value_hash(value)
        common = {
            "probe_scope": "parameter_domain",
            "parameter": parameter,
            "allowed_values_hash": allowed_values_hash,
            "value_hash": value_hash,
            "contract_hash": contract_hash,
        }
        for context in contexts:
            context_body = {
                "produces": context.produces,
                "subgoal_id": context.subgoal_id,
                "config": context.config,
            }
            context_id = _sha256(_strict_canonical_json(context_body))
            config = {**context.config, parameter: value}
            suffix = (
                "parameter_domain",
                parameter,
                allowed_values_hash,
                value_hash,
                context_id,
            )
            specs.append(
                ProbeSpec(
                    probe_id=probe_id_for(card.name, "schema", *suffix),
                    kind="schema",
                    description=(
                        f"confirm parameter '{parameter}' value {value_hash[:8]} "
                        f"preserves {context.described}"
                    ),
                    inputs=dict(valid),
                    expectations={
                        **common,
                        "output_context_id": context_id,
                        "produces": list(context.produces),
                        "validate_schema": True,
                    },
                    config=config,
                    subgoal_id=context.subgoal_id,
                )
            )
            specs.append(
                ProbeSpec(
                    probe_id=probe_id_for(card.name, "smoke", *suffix),
                    kind="smoke",
                    description=(
                        f"confirm parameter '{parameter}' value {value_hash[:8]} "
                        f"produces a non-vacuous {context.described}"
                    ),
                    inputs=dict(smoke_inputs),
                    expectations={
                        **common,
                        "output_context_id": context_id,
                        "produces": list(context.produces),
                        "require_non_empty": True,
                        "input_source": input_source,
                    },
                    config=config,
                    subgoal_id=context.subgoal_id,
                )
            )

        resource_suffix = (
            "parameter_domain",
            parameter,
            allowed_values_hash,
            value_hash,
        )
        specs.append(
            ProbeSpec(
                probe_id=probe_id_for(card.name, "resource", *resource_suffix),
                kind="resource",
                description=(
                    f"confirm parameter '{parameter}' value {value_hash[:8]} "
                    "honours the declared resource budget"
                ),
                inputs=dict(valid),
                expectations={
                    **common,
                    "output_context_id": "resource",
                    "limits": _resource_limits(card),
                },
                config={parameter: value},
            )
        )
    return specs


def _invalid_input_specs(
    card: CapabilityCard,
    registry: Optional[TypeRegistry],
    valid: dict[str, Any],
) -> list[ProbeSpec]:
    specs: list[ProbeSpec] = []
    name = card.name

    if not card.io.consumes:
        specs.append(
            ProbeSpec(
                probe_id=probe_id_for(name, "invalid_input", "config"),
                kind="invalid_input",
                description=(
                    f"'{name}' consumes no artifacts, so refusal is probed on the "
                    "configuration surface: it is given an unknown parameter with "
                    "a nonsense value and must not proceed as if nothing happened"
                ),
                inputs={},
                expectations={
                    "must_reject": True,
                    "corruption": "unknown_config_parameter",
                    "defect": "an unknown configuration parameter with a nonsense value",
                },
                config={"__agentcoop_probe_unknown_parameter__": ["not", "a", "value"]},
            )
        )
        return specs

    for declared in card.io.consumes:
        resolved = resolve_type(declared, registry)
        base = synthesize_payload(resolved)
        for corruption in applicable_corruptions(resolved):
            payload, facet_overrides, described = corrupt_payload(
                base, resolved, corruption
            )
            broken = probe_artifact(
                resolved, payload, facet_overrides=facet_overrides
            )
            inputs = dict(valid)
            inputs[resolved.name] = broken
            specs.append(
                ProbeSpec(
                    probe_id=probe_id_for(
                        name, "invalid_input", resolved.name, corruption.value
                    ),
                    kind="invalid_input",
                    description=(
                        f"give '{name}' {described} as '{resolved.name}' and "
                        "require an explicit refusal"
                    ),
                    inputs=inputs,
                    expectations={
                        "must_reject": True,
                        "corruption": corruption.value,
                        "target_type": resolved.name,
                        "defect": described,
                    },
                )
            )
    return specs


__all__ = [
    "standard_suite",
    "parameter_domain_suite",
    "applicable_corruptions",
    "resolve_type",
    "OutputContext",
    "DEFAULT_RESOURCE_LIMIT_S",
    "DETERMINISM_SEED",
]
