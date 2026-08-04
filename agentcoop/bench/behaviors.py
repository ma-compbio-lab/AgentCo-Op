"""Synthetic component behaviours, so a benchmark suite can stay pure data.

Every behaviour here is a deterministic function of its inputs and parameters.
That is not a simplification for convenience — it is what makes the benchmark
able to answer the questions it is built for. If a component's output varied
run to run, "the compiled workflow scored higher than the single-agent
baseline" would be a statement about sampling noise, and "the diagnoser blamed
the right node" would be unfalsifiable.

The behaviours are chosen to make composition genuinely necessary in some
tasks and genuinely harmful in others:

* ``map_namespace`` has a *coverage* parameter, so an adapter step can be
  lossy. Composition that inserts one is paying a real price.
* ``lookup`` refuses inputs whose facets it does not recognise, when
  configured strictly. This is what makes a namespace mismatch a real failure
  rather than a bookkeeping annoyance.
* ``generalist`` dispatches on subgoal, and takes a per-subgoal ``quality``
  map. A generalist that is competent everywhere makes composition pointless;
  one that is weak at a specific subgoal makes it necessary. Both are
  expressible, which is the point.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, NamedTuple, Optional

from agentcoop.components.base import (
    Clock,
    ComponentAdapter,
    Invocation,
    InvocationResult,
    draft_artifact,
    error_line,
    finalize_outputs,
    input_ids,
    zero_clock,
)
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.faults import FaultClass


class Emission(NamedTuple):
    """What a behaviour decided to do.

    ``refusal`` being set is a *successful* outcome for the benchmark's
    purposes — a component that refuses bad input is the well-behaved one — so
    it is modelled explicitly rather than as an exception.
    """

    payloads: dict[str, Any]
    refusal: Optional[str] = None
    fault_class: FaultClass = FaultClass.ARTIFACT_CONTRACT
    #: Facets the component *emits* at run time — these become observed facets.
    #: ``None`` rather than ``{}`` so the shared default cannot be mutated.
    facets: Optional[dict[str, dict[str, str]]] = None
    cost: Optional[CostProfile] = None

    def facets_for(self, type_name: str) -> dict[str, str]:
        return dict((self.facets or {}).get(type_name, {}))


BehaviorFn = Callable[[Invocation, dict[str, Any]], Emission]

_BEHAVIORS: dict[str, BehaviorFn] = {}


def register_behavior(name: str) -> Callable[[BehaviorFn], BehaviorFn]:
    def decorate(fn: BehaviorFn) -> BehaviorFn:
        _BEHAVIORS[name] = fn
        return fn

    return decorate


def behavior_names() -> list[str]:
    return sorted(_BEHAVIORS)


def get_behavior(name: str) -> BehaviorFn:
    fn = _BEHAVIORS.get(name)
    if fn is None:
        raise KeyError(
            f"unknown behaviour '{name}' (registered: {', '.join(behavior_names())})"
        )
    return fn


# ---------------------------------------------------------------------------
# Helpers shared by behaviours
# ---------------------------------------------------------------------------


def _sole_input(inv: Invocation, preferred: Optional[str] = None) -> Optional[Artifact]:
    if preferred and preferred in inv.inputs:
        return inv.inputs[preferred]
    if not inv.inputs:
        return None
    return inv.inputs[sorted(inv.inputs)[0]]


def _items(payload: Any, key: str) -> Optional[list[Any]]:
    """Pull the working list out of a payload, or ``None`` if it is not there."""
    if isinstance(payload, dict):
        value = payload.get(key)
        return list(value) if isinstance(value, list) else None
    if isinstance(payload, list):
        return list(payload)
    return None


def _truncate(items: list[Any], quality: float) -> list[Any]:
    """Keep a deterministic prefix of ``items`` sized by ``quality``.

    A prefix rather than a sample: a benchmark whose difficulty depends on an
    RNG cannot support a claim about which system is better.
    """
    if quality >= 1.0:
        return list(items)
    if quality <= 0.0:
        return []
    return list(items)[: max(1, math.floor(len(items) * quality))]


def _facet_refusal(
    artifact: Artifact, required: Mapping[str, str], component: str
) -> Optional[str]:
    for key, expected in sorted(required.items()):
        actual = artifact.facets.get(key)
        if actual is None:
            return (
                f"'{component}' requires the '{key}' facet to be declared on "
                f"'{artifact.type_name}' and it is absent; proceeding would mean "
                "guessing what the data means"
            )
        if actual != expected:
            return (
                f"'{component}' requires {key}={expected} on '{artifact.type_name}' "
                f"but received {key}={actual}"
            )
    return None


# ---------------------------------------------------------------------------
# Behaviours
# ---------------------------------------------------------------------------


@register_behavior("identity")
def _identity(inv: Invocation, params: dict[str, Any]) -> Emission:
    """Echo the input payload under the declared output type."""
    output_type = params["output_type"]
    art = _sole_input(inv, params.get("input_type"))
    if art is None:
        return Emission({}, refusal="no input artifact was supplied")
    return Emission(
        {output_type: art.payload}, facets={output_type: dict(params.get("emits", {}))}
    )


@register_behavior("map_namespace")
def _map_namespace(inv: Invocation, params: dict[str, Any]) -> Emission:
    """Translate identifiers between namespaces, losing whatever does not map.

    Coverage below 1.0 is the honest model of a real identifier mapper. It is
    also what gives ``insert_adapter`` a genuine cost, so the compiler's
    decision to add one has to be justified rather than free.
    """
    output_type = params["output_type"]
    key = params.get("key", "items")
    table: dict[str, str] = params.get("table", {})
    strict = bool(params.get("strict", True))
    art = _sole_input(inv, params.get("input_type"))
    if art is None:
        return Emission({}, refusal="no input artifact was supplied")

    if strict:
        refusal = _facet_refusal(art, params.get("requires_facets", {}), inv.component)
        if refusal:
            return Emission({}, refusal=refusal)

    items = _items(art.payload, key)
    if items is None:
        return Emission(
            {},
            refusal=(
                f"input '{art.type_name}' has no '{key}' list to map; the payload "
                "does not match the declared contract"
            ),
        )

    mapped = [table[i] for i in items if i in table]
    payload = {key: mapped, "n": len(mapped), "dropped": len(items) - len(mapped)}
    return Emission(
        {output_type: payload}, facets={output_type: dict(params.get("emits", {}))}
    )


@register_behavior("lookup")
def _lookup(inv: Invocation, params: dict[str, Any]) -> Emission:
    """Expand each input item through a fixed table (the 'analysis' step)."""
    output_type = params["output_type"]
    in_key = params.get("key", "items")
    out_key = params.get("output_key", "results")
    table: dict[str, list[Any]] = params.get("table", {})
    quality = float(params.get("quality", 1.0))
    strict = bool(params.get("strict", True))

    art = _sole_input(inv, params.get("input_type"))
    if art is None:
        return Emission({}, refusal="no input artifact was supplied")
    if strict:
        refusal = _facet_refusal(art, params.get("requires_facets", {}), inv.component)
        if refusal:
            return Emission({}, refusal=refusal)

    items = _items(art.payload, in_key)
    if items is None:
        return Emission(
            {},
            refusal=(
                f"input '{art.type_name}' has no '{in_key}' list; the payload does "
                "not match the declared contract"
            ),
        )

    results: list[Any] = []
    for item in items:
        for value in table.get(item, []):
            if value not in results:
                results.append(value)
    results = _truncate(results, quality)
    payload = {out_key: results, "n": len(results)}
    return Emission(
        {output_type: payload}, facets={output_type: dict(params.get("emits", {}))}
    )


@register_behavior("reduce")
def _reduce(inv: Invocation, params: dict[str, Any]) -> Emission:
    """Combine every input into one report artifact."""
    output_type = params["output_type"]
    out_key = params.get("output_key", "summary")
    quality = float(params.get("quality", 1.0))

    if not inv.inputs:
        return Emission({}, refusal="nothing to summarize")

    sections: list[dict[str, Any]] = []
    for type_name in sorted(inv.inputs):
        art = inv.inputs[type_name]
        sections.append({"source": type_name, "content": art.payload})
    sections = _truncate(sections, quality)
    payload = {out_key: sections, "n_sources": len(sections)}
    return Emission(
        {output_type: payload}, facets={output_type: dict(params.get("emits", {}))}
    )


@register_behavior("constant")
def _constant(inv: Invocation, params: dict[str, Any]) -> Emission:
    """Emit a fixed payload regardless of input.

    Used for graders that always agree and for the 'extra step that adds
    nothing', which is how a ``multi_harmful`` task is made harmful.
    """
    output_type = params["output_type"]
    return Emission(
        {output_type: params.get("payload", {"ok": True})},
        facets={output_type: dict(params.get("emits", {}))},
    )


@register_behavior("generalist")
def _generalist(inv: Invocation, params: dict[str, Any]) -> Emission:
    """One component that claims to serve every subgoal.

    Dispatches on ``inv.subgoal_id`` to a sub-behaviour, so a single card can
    stand in for the 'just use one strong model' baseline. Per-subgoal
    ``quality`` is what decides whether that baseline is adequate — and both
    answers have to be expressible, or the benchmark is rigged.
    """
    routes: dict[str, dict[str, Any]] = params.get("routes", {})
    quality: dict[str, float] = params.get("quality", {})
    subgoal = inv.subgoal_id or params.get("default_subgoal", "")

    route = routes.get(subgoal) or routes.get("*")
    if route is None:
        return Emission(
            {},
            refusal=(
                f"'{inv.component}' has no route for subgoal '{subgoal}'; it claims "
                "the capability but cannot discharge it"
            ),
            fault_class=FaultClass.CAPABILITY_MISMATCH,
        )

    sub_params = dict(route)
    behavior = sub_params.pop("behavior", "identity")
    if subgoal in quality:
        sub_params["quality"] = quality[subgoal]
    return get_behavior(behavior)(inv, sub_params)


@register_behavior("failing")
def _failing(inv: Invocation, params: dict[str, Any]) -> Emission:
    """Always refuses. Used to make a component unusable in a controlled way."""
    return Emission(
        {},
        refusal=params.get("reason", "this component always fails"),
        fault_class=FaultClass(params.get("fault_class", FaultClass.TOOL_FAILURE.value)),
    )


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class SyntheticAdapter:
    """Runs a registered behaviour behind the standard adapter boundary.

    It goes through :func:`finalize_outputs` exactly like a real adapter, so
    provenance, emptiness notes, and observed-versus-declared facet handling
    behave identically. A benchmark whose components took a shortcut around
    that boundary would be testing a different system than the one shipped.
    """

    def __init__(
        self,
        name: str,
        behavior: str,
        params: Optional[dict[str, Any]] = None,
        *,
        declared_facets: Optional[Mapping[str, Mapping[str, str]]] = None,
        cost: Optional[CostProfile] = None,
        deterministic: bool = True,
        shadow_safe: bool = True,
        clock: Clock = zero_clock,
    ) -> None:
        self.name = name
        self.behavior = behavior
        self.params = dict(params or {})
        self.declared_facets = {k: dict(v) for k, v in (declared_facets or {}).items()}
        self.cost = cost or CostProfile()
        self._deterministic = deterministic
        self._shadow_safe = shadow_safe
        self._clock = clock
        self.invocations: list[Invocation] = []

    def behavior_contract(self) -> BehaviorContract:
        return BehaviorContract(
            deterministic=self._deterministic,
            idempotent=self._deterministic,
            retry_safe=True,
            shadow_safe=self._shadow_safe,
        )

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        self.invocations.append(inv)
        try:
            emission = get_behavior(self.behavior)(inv, self.params)
        except KeyError as exc:
            return InvocationResult(
                ok=False,
                errors=[error_line(FaultClass.CONFIGURATION, str(exc))],
                exit_code=2,
            )

        elapsed = self._clock() - started
        cost = self.cost + CostProfile(latency_s=elapsed)

        if emission.refusal is not None:
            return InvocationResult(
                ok=False,
                errors=[error_line(emission.fault_class, emission.refusal)],
                exit_code=1,
                cost=cost,
            )

        drafts = {
            type_name: draft_artifact(
                type_name=type_name,
                payload=payload,
                facets=emission.facets_for(type_name),
            )
            for type_name, payload in emission.payloads.items()
        }
        finalized = finalize_outputs(
            drafts,
            declared_facets=self.declared_facets,
            producer=inv.producer_id,
            derived_from=input_ids(inv),
        )
        return InvocationResult(
            ok=True,
            outputs=finalized.outputs,
            cost=cost + (emission.cost or CostProfile()),
            observed_facets=finalized.observed_facets,
            exit_code=0,
        )


def build_adapter(spec: Any, *, clock: Clock = zero_clock) -> ComponentAdapter:
    """Materialize a :class:`~agentcoop.bench.task.ComponentSpec` into an adapter.

    Facets the card *declares* on its produced types are passed as declared
    facets, not as emitted ones, so a synthetic component is subject to the
    same "declaration is not observation" rule as a real external repository.
    Behaviours that want a facet to count as observed must emit it explicitly
    via ``params["emits"]``.
    """
    declared = {t.name: dict(t.facets) for t in spec.card.io.produces}
    behavior_contract = spec.card.behavior
    return SyntheticAdapter(
        spec.card.name,
        spec.behavior,
        spec.params,
        declared_facets=declared,
        cost=spec.card.declared_cost,
        deterministic=behavior_contract.deterministic,
        shadow_safe=behavior_contract.shadow_safe,
        clock=clock,
    )


__all__ = [
    "Emission",
    "BehaviorFn",
    "register_behavior",
    "behavior_names",
    "get_behavior",
    "SyntheticAdapter",
    "build_adapter",
]
