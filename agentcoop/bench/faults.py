"""Injecting known faults so localization and diagnosis become measurable.

The claim "our diagnoser blames the right thing" is unfalsifiable unless
something other than the diagnoser knows what the right thing is. So every
injection here is authored together with its ground-truth blame target, and
the harness scores the diagnosis against that answer sheet rather than against
its own confidence.

The mechanisms are chosen to span the fault taxonomy in a specific way: half
of them are *loud* (the run visibly breaks) and half are *silent* (the run
completes, the exit codes are zero, and the result is wrong). Loud faults are
the easy case — any system that reads exit codes finds them. The silent ones
are the discriminating measurement, which is why
:meth:`~agentcoop.bench.task.BenchSuite.coverage_defects` refuses a suite that
contains only loud faults.

Injection wraps the adapter rather than editing the component, so the same
declared card is in play with and without the fault. Otherwise a system could
"detect" the fault by noticing the card changed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from agentcoop.components.base import (
    ComponentAdapter,
    Invocation,
    InvocationResult,
    behavior_of,
    error_line,
)
from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.faults import FaultClass


class Mechanism(str, Enum):
    """How a fault is planted. Values match ``InjectedFault.mechanism``."""

    #: An emitted artifact's semantic facet is silently rewritten. Schemas
    #: still validate; the meaning is wrong. The canonical silent fault.
    NAMESPACE_CORRUPTION = "namespace_corruption"
    #: The component's runtime is unavailable. Loud, and correctly blamed on
    #: the environment rather than on the component's logic.
    DEPENDENCY_BREAK = "dependency_break"
    #: Exit code 0, empty result. The failure mode that gets believed.
    SILENT_EMPTY_OUTPUT = "silent_empty_output"
    #: The component answers from the first input it ever saw, ignoring the
    #: current one. Silent, and only detectable through lineage.
    STALE_CACHE = "stale_cache"
    #: The evaluator passes everything, so downstream verification is theatre.
    MISCALIBRATED_GRADER = "miscalibrated_grader"
    #: A spurious ordering constraint in the task specification, forcing a
    #: sequence where the work is independent. A coordination fault that no
    #: amount of node-level repair can fix.
    NEEDLESS_SERIALIZATION = "needless_serialization"
    #: The result is cut short but still well-formed — a partial answer that
    #: looks complete.
    TRUNCATED_OUTPUT = "truncated_output"


#: Mechanisms that leave exit codes clean. Scored separately by the harness.
SILENT_MECHANISMS = frozenset(
    {
        Mechanism.NAMESPACE_CORRUPTION,
        Mechanism.SILENT_EMPTY_OUTPUT,
        Mechanism.STALE_CACHE,
        Mechanism.MISCALIBRATED_GRADER,
        Mechanism.TRUNCATED_OUTPUT,
    }
)

#: The fault class a correct diagnosis should reach for each mechanism.
EXPECTED_FAULT_CLASS: dict[Mechanism, FaultClass] = {
    Mechanism.NAMESPACE_CORRUPTION: FaultClass.ARTIFACT_CONTRACT,
    Mechanism.DEPENDENCY_BREAK: FaultClass.ENVIRONMENT,
    Mechanism.SILENT_EMPTY_OUTPUT: FaultClass.ARTIFACT_CONTRACT,
    Mechanism.STALE_CACHE: FaultClass.STALE_STATE,
    Mechanism.MISCALIBRATED_GRADER: FaultClass.EVALUATOR_FAILURE,
    Mechanism.NEEDLESS_SERIALIZATION: FaultClass.COORDINATION,
    Mechanism.TRUNCATED_OUTPUT: FaultClass.ARTIFACT_CONTRACT,
}


def is_silent(mechanism: str) -> bool:
    try:
        return Mechanism(mechanism) in SILENT_MECHANISMS
    except ValueError:
        return False


def expected_fault_class(mechanism: str) -> Optional[FaultClass]:
    try:
        return EXPECTED_FAULT_CLASS[Mechanism(mechanism)]
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Adapter-level injection
# ---------------------------------------------------------------------------


class FaultyAdapter:
    """Wraps a healthy adapter and corrupts what it returns.

    Deliberately preserves ``name`` and the wrapped adapter's behaviour
    contract, so nothing upstream can tell the difference by inspection. The
    fault has to be found in the evidence, which is the whole exercise.
    """

    def __init__(
        self,
        inner: ComponentAdapter,
        mechanism: Mechanism,
        params: Optional[dict[str, Any]] = None,
        *,
        fault_id: str = "",
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.mechanism = mechanism
        self.params = dict(params or {})
        self.fault_id = fault_id
        self._first_result: Optional[InvocationResult] = None

    def behavior_contract(self) -> BehaviorContract:
        contract = behavior_of(self.inner)
        if contract is not None:
            return contract
        return BehaviorContract()

    async def invoke(self, inv: Invocation) -> InvocationResult:
        if self.mechanism is Mechanism.DEPENDENCY_BREAK:
            missing = self.params.get("package", "libagentcoop-probe")
            return InvocationResult(
                ok=False,
                errors=[
                    error_line(
                        FaultClass.ENVIRONMENT,
                        f"cannot start '{self.name}': required dependency "
                        f"'{missing}' is not installed in this environment",
                    )
                ],
                exit_code=127,
                cost=CostProfile(),
            )

        if self.mechanism is Mechanism.STALE_CACHE and self._first_result is not None:
            # Answers from the first input it ever saw. The payload is a
            # perfectly valid artifact; only its lineage betrays it.
            return self._first_result

        result = await self.inner.invoke(inv)

        if self.mechanism is Mechanism.STALE_CACHE and self._first_result is None:
            self._first_result = result
            return result

        if not result.ok:
            return result

        if self.mechanism is Mechanism.NAMESPACE_CORRUPTION:
            return self._corrupt_facets(result)
        if self.mechanism is Mechanism.SILENT_EMPTY_OUTPUT:
            return self._empty(result)
        if self.mechanism is Mechanism.TRUNCATED_OUTPUT:
            return self._truncate(result)
        if self.mechanism is Mechanism.MISCALIBRATED_GRADER:
            return self._always_pass(result)
        return result

    # -- mechanisms --------------------------------------------------------

    def _corrupt_facets(self, result: InvocationResult) -> InvocationResult:
        facet = self.params.get("facet", "namespace")
        value = self.params.get("value", "ENSEMBL")
        targets = self.params.get("types") or list(result.outputs)
        outputs = dict(result.outputs)
        observed = {k: dict(v) for k, v in result.observed_facets.items()}
        for type_name in targets:
            art = outputs.get(type_name)
            if art is None:
                continue
            facets = {**art.facets, facet: value}
            # Re-finalize: the content hash covers facets, so a corrupted
            # artifact must not keep the clean one's identity.
            corrupted = art.model_copy(
                update={"facets": facets, "content_hash": ""}
            ).finalize()
            corrupted.artifact_id = (
                f"{corrupted.producer}::{type_name}::{corrupted.content_hash}"
            )
            outputs[type_name] = corrupted
            if type_name in observed:
                observed[type_name][facet] = value
        return result.model_copy(update={"outputs": outputs, "observed_facets": observed})

    def _empty(self, result: InvocationResult) -> InvocationResult:
        outputs = {}
        for type_name, art in result.outputs.items():
            emptied = art.model_copy(
                update={"payload": _empty_like(art.payload), "content_hash": ""}
            ).finalize()
            emptied.artifact_id = (
                f"{emptied.producer}::{type_name}::{emptied.content_hash}"
            )
            outputs[type_name] = emptied
        return result.model_copy(update={"outputs": outputs})

    def _truncate(self, result: InvocationResult) -> InvocationResult:
        keep = int(self.params.get("keep", 1))
        outputs = {}
        for type_name, art in result.outputs.items():
            payload = art.payload
            if isinstance(payload, dict):
                payload = {
                    k: (v[:keep] if isinstance(v, list) else v)
                    for k, v in payload.items()
                }
            elif isinstance(payload, list):
                payload = payload[:keep]
            truncated = art.model_copy(
                update={"payload": payload, "content_hash": ""}
            ).finalize()
            truncated.artifact_id = (
                f"{truncated.producer}::{type_name}::{truncated.content_hash}"
            )
            outputs[type_name] = truncated
        return result.model_copy(update={"outputs": outputs})

    def _always_pass(self, result: InvocationResult) -> InvocationResult:
        verdict_key = self.params.get("verdict_key", "passed")
        outputs = {}
        for type_name, art in result.outputs.items():
            payload = art.payload
            if isinstance(payload, dict):
                payload = {**payload, verdict_key: True, "score": 1.0}
            passing = art.model_copy(
                update={"payload": payload, "content_hash": ""}
            ).finalize()
            passing.artifact_id = (
                f"{passing.producer}::{type_name}::{passing.content_hash}"
            )
            outputs[type_name] = passing
        return result.model_copy(update={"outputs": outputs})


def _empty_like(payload: Any) -> Any:
    """An empty payload of the same *shape*, so the envelope still looks healthy."""
    if isinstance(payload, dict):
        return {
            k: ([] if isinstance(v, list) else 0 if isinstance(v, int) else v)
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return []
    return payload


# ---------------------------------------------------------------------------
# Dossier-level injection
# ---------------------------------------------------------------------------


def serialize_subgoals(
    dossier: TaskEvidenceDossier, first: str, second: str
) -> TaskEvidenceDossier:
    """Add a dependency that the artifact flow does not require.

    This is the one fault that cannot be planted in a component, because it is
    not in one: two independent analyses are forced into a chain, doubling
    latency and creating a failure path that need not exist. Node-level repair
    can never fix it, which is exactly why the fault taxonomy routes it to
    ``global_redesign``.
    """
    updated = dossier.model_copy(deep=True)
    for subgoal in updated.subgoals:
        if subgoal.subgoal_id == second and first not in subgoal.depends_on:
            subgoal.depends_on = [*subgoal.depends_on, first]
    return updated


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install_faults(env: Any, faults: list[Any]) -> Any:
    """Install every fault into ``env``, returning the updated environment.

    Unknown mechanisms are recorded as notes rather than skipped silently: a
    benchmark that quietly declines to inject the fault it advertised would
    report inflated detection rates.
    """
    notes = list(env.notes)
    active = []
    dossier = env.task.dossier

    for fault in faults:
        try:
            mechanism = Mechanism(fault.mechanism)
        except ValueError:
            notes.append(
                f"fault '{fault.fault_id}' declares unknown mechanism "
                f"'{fault.mechanism}' and was NOT injected; any detection result "
                "for it is meaningless"
            )
            continue

        if mechanism is Mechanism.NEEDLESS_SERIALIZATION:
            first = fault.params.get("first")
            second = fault.params.get("second") or fault.target
            if not first or not second:
                notes.append(
                    f"fault '{fault.fault_id}' needs 'first' and 'second' subgoal "
                    "ids and was NOT injected"
                )
                continue
            dossier = serialize_subgoals(dossier, first, second)
            active.append(fault)
            notes.append(
                f"{fault.fault_id}: forced '{second}' to wait on '{first}'"
            )
            continue

        if not env.adapters.has(fault.target):
            notes.append(
                f"fault '{fault.fault_id}' targets component '{fault.target}', which "
                "is not in this task; it was NOT injected"
            )
            continue

        env.adapters.register(
            FaultyAdapter(
                env.adapters.get(fault.target),
                mechanism,
                fault.params,
                fault_id=fault.fault_id,
            )
        )
        active.append(fault)
        notes.append(f"{fault.fault_id}: wrapped '{fault.target}' with {mechanism.value}")

    task = env.task
    if dossier is not task.dossier:
        task = task.model_copy(update={"dossier": dossier})

    return env.model_copy(
        update={"task": task, "active_faults": active, "notes": notes}
    )


__all__ = [
    "Mechanism",
    "SILENT_MECHANISMS",
    "EXPECTED_FAULT_CLASS",
    "is_silent",
    "expected_fault_class",
    "FaultyAdapter",
    "serialize_subgoals",
    "install_faults",
]
