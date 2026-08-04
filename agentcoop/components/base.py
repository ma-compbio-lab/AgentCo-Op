"""The invocation boundary: the only place AgentCo-Op touches the outside world.

Everything above this layer — probes, compilation, execution, diagnosis,
repair — reasons about components purely through :class:`Invocation` and
:class:`InvocationResult`. That is deliberate. If an adapter is allowed to
smuggle information out of band (a global, a side channel, a silent host
fallback), then the capability cards stop describing what actually ran and
every downstream claim about certification, isolation, or determinism becomes
unfalsifiable.

Three conventions in this module carry real weight:

**Observed versus declared facets.**
    :attr:`InvocationResult.observed_facets` holds only facets the adapter can
    *attest to* because it saw the emitted data: facets the producing code
    stamped onto the artifact at run time, or facets a deterministic observer
    read back off the payload. Facets that merely came from static adapter
    configuration are applied to the artifact (so downstream typing works) but
    are **excluded** from ``observed_facets``. The probe layer promotes
    ``observed_facets`` into ``CapabilityCard.empirical.confirmed_facets``, so
    anything in there must have been seen rather than read from a README. A
    component that emits bare artifacts therefore stays UNDERSPECIFIED instead
    of being credited with the facets its card claims.

**Fault-shaped error lines.**
    ``errors`` is a list of strings, but each line produced by this package is
    prefixed with a :class:`FaultClass` tag via :func:`error_line`. Diagnosis
    needs to distinguish "docker is missing" from "the tool crashed" without
    regex-guessing over English prose, and the adapter is the only layer that
    still knows which it was.

**Emptiness notes.**
    An adapter that returns ``ok=True`` with an empty payload is the single
    most dangerous failure mode in this system, because "the workflow ran to
    completion" then gets mistaken for "the result is good". Adapters annotate
    emitted artifacts with :data:`EMPTY_PAYLOAD_NOTE` / ``empty_field:<path>``
    notes so the emptiness is recorded at the boundary where it is observable,
    rather than being inferred much later from a missing conclusion.

Determinism note: adapters measure latency with an injectable clock. The
measurement lands in :class:`CostProfile` and is never read back by any
decision path in this package. Artifact content hashes are computed from
type, facets, and payload only, so a deterministic component produces
identical hashes across runs regardless of how long it took.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import Artifact, FacetSet
from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.dossier import ResourceLimits
from agentcoop.ir.faults import FaultClass

# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------

#: A monotonic source of seconds. Injectable so tests can pin it.
Clock = Callable[[], float]


def perf_clock() -> float:
    """Monotonic clock used for cost accounting only, never for decisions."""
    return time.perf_counter()


def zero_clock() -> float:
    """A clock that never advances.

    Pass this when a whole :class:`InvocationResult` (not just its artifact
    hashes) must be byte-identical across runs — for example when recording a
    golden fixture.
    """
    return 0.0


# ---------------------------------------------------------------------------
# Error-line convention
# ---------------------------------------------------------------------------


def error_line(fault_class: FaultClass, message: str) -> str:
    """Format a fault-shaped error line.

    The tag is machine-readable so :mod:`agentcoop.diagnose` can class an
    error without pattern-matching prose. A symptom string alone would force
    the diagnoser back into the "test failed -> retry the node" reflex this
    rebuild exists to remove.
    """
    return f"[{fault_class.value}] {message}"


def parse_error_line(line: str) -> tuple[Optional[FaultClass], str]:
    """Split a line produced by :func:`error_line` into its class and message."""
    match = re.match(r"^\[([a-z_]+)\]\s*(.*)$", line, flags=re.DOTALL)
    if not match:
        return None, line
    try:
        return FaultClass(match.group(1)), match.group(2)
    except ValueError:
        return None, line


def errors_of_class(errors: Iterable[str], fault_class: FaultClass) -> list[str]:
    """Messages from ``errors`` tagged with ``fault_class``."""
    out: list[str] = []
    for line in errors:
        cls, message = parse_error_line(line)
        if cls is fault_class:
            out.append(message)
    return out


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class Invocation(BaseModel):
    """One request to run a component.

    ``seed`` is the determinism control: an adapter that claims determinism
    must produce identical artifact content hashes for identical
    ``(inputs, config, seed)``. The determinism probe holds ``seed`` fixed and
    compares hashes, so an adapter that ignores it and reaches for global
    randomness will be caught rather than assumed honest.
    """

    model_config = ConfigDict(extra="forbid")

    component: str
    subgoal_id: Optional[str] = None
    #: Keyed by artifact *type* name, matching ``IOContract.consumes``.
    inputs: dict[str, Artifact] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    workdir: Optional[Path] = None
    limits: Optional[ResourceLimits] = None
    seed: int = 0

    @property
    def producer_id(self) -> str:
        """Identity stamped onto emitted artifacts.

        The execution engine passes ``node_id`` in ``config`` when the same
        component appears at more than one node, so lineage stays unambiguous.
        """
        node_id = self.config.get("node_id")
        return str(node_id) if node_id else self.component

    def timeout_s(self, default: float) -> float:
        """Effective wall-time budget: config override, then dossier limits."""
        configured = self.config.get("timeout_s")
        if isinstance(configured, (int, float)) and configured > 0:
            return float(configured)
        if self.limits is not None and self.limits.max_wall_time_s:
            return float(self.limits.max_wall_time_s)
        return default


class InvocationResult(BaseModel):
    """What came back.

    ``ok`` means "this component discharged its contract", not "the workflow
    may proceed" and certainly not "the answer is good". Whether the result is
    any good is decided later, against the evaluation contract.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    #: Keyed by artifact *type* name, matching ``IOContract.produces``.
    outputs: dict[str, Artifact] = Field(default_factory=dict)
    cost: CostProfile = Field(default_factory=CostProfile)
    logs: str = ""
    errors: list[str] = Field(default_factory=list)
    exit_code: Optional[int] = None
    #: Facets actually observed on emitted artifacts; probes promote these
    #: into the capability card. Observation beats documentation.
    observed_facets: dict[str, FacetSet] = Field(default_factory=dict)

    def fault_classes(self) -> list[FaultClass]:
        """Distinct fault classes tagged on ``errors``, in first-seen order."""
        seen: list[FaultClass] = []
        for line in self.errors:
            cls, _ = parse_error_line(line)
            if cls is not None and cls not in seen:
                seen.append(cls)
        return seen

    def has_fault(self, fault_class: FaultClass) -> bool:
        return fault_class in self.fault_classes()


@runtime_checkable
class ComponentAdapter(Protocol):
    """The one thing every invocable component must be."""

    name: str

    async def invoke(self, inv: Invocation) -> InvocationResult: ...


@runtime_checkable
class DeclaresBehavior(Protocol):
    """Optional adapter capability: state your own behaviour contract.

    Kept separate from :class:`ComponentAdapter` so the core protocol stays
    exactly as specified. Adapters wrapping non-deterministic or
    side-effecting executors (LLMs, coding agents, humans) implement this so
    that ``BehaviorContract.shadow_safe`` and ``.deterministic`` are answered
    by the adapter that knows the truth instead of by a hand-written card.
    """

    def behavior_contract(self) -> BehaviorContract: ...


def behavior_of(adapter: object) -> Optional[BehaviorContract]:
    """Behaviour contract an adapter declares about itself, if any.

    Returns ``None`` rather than a permissive default: an adapter that says
    nothing must not be silently assumed deterministic and shadow-safe.
    """
    method = getattr(adapter, "behavior_contract", None)
    if callable(method):
        contract = method()
        if isinstance(contract, BehaviorContract):
            return contract
    return None


class AdapterRegistry:
    """Name -> adapter. A plain object, never a global.

    Probe runs, shadow validation, and the benchmark harness each build their
    own registry so a component certified in one context cannot leak its
    adapter into another.
    """

    def __init__(self, adapters: Iterable[ComponentAdapter] = ()) -> None:
        self._adapters: dict[str, ComponentAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ComponentAdapter) -> None:
        """Register (or replace) an adapter under ``adapter.name``."""
        name = getattr(adapter, "name", "")
        if not name:
            raise ValueError("adapter must expose a non-empty 'name'")
        if not callable(getattr(adapter, "invoke", None)):
            raise TypeError(f"adapter '{name}' does not implement invoke()")
        self._adapters[name] = adapter

    def get(self, name: str) -> ComponentAdapter:
        adapter = self._adapters.get(name)
        if adapter is None:
            known = ", ".join(self.names()) or "<none>"
            raise KeyError(f"no adapter registered for '{name}' (registered: {known})")
        return adapter

    def has(self, name: str) -> bool:
        return name in self._adapters

    def names(self) -> list[str]:
        """Sorted, so iteration order never depends on registration order."""
        return sorted(self._adapters)


# ---------------------------------------------------------------------------
# Result constructors
# ---------------------------------------------------------------------------


def failure_result(
    fault_class: FaultClass,
    message: str,
    *,
    logs: str = "",
    exit_code: Optional[int] = None,
    cost: Optional[CostProfile] = None,
    extra_errors: Iterable[str] = (),
) -> InvocationResult:
    """A failed invocation whose error carries its fault class."""
    errors = [error_line(fault_class, message), *extra_errors]
    return InvocationResult(
        ok=False,
        outputs={},
        cost=cost or CostProfile(),
        logs=logs,
        errors=errors,
        exit_code=exit_code,
    )


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def artifact_id_for(type_name: str, content_hash: str, producer: str = "") -> str:
    """Deterministic, content-addressed artifact id.

    Includes the producer so two nodes emitting byte-identical artifacts stay
    distinguishable during backward slicing; excludes anything time-varying so
    re-running a deterministic workflow yields the same lineage graph.
    """
    return f"{producer}::{type_name}::{content_hash}" if producer else f"{type_name}::{content_hash}"


def draft_artifact(
    *,
    type_name: str,
    payload: Any = None,
    facets: Optional[Mapping[str, str]] = None,
    producer: str = "",
    derived_from: Iterable[str] = (),
    path: Optional[str] = None,
    step: int = 0,
    notes: Iterable[str] = (),
    content_hash: Optional[str] = None,
) -> Artifact:
    """An artifact with identity deliberately left blank.

    :func:`finalize_outputs` fills in ``content_hash`` and ``artifact_id``
    *after* facet observation, so the identity reflects what was actually
    emitted rather than what was assumed before the payload was inspected.
    Pass ``content_hash`` only when a better content identity exists — for
    file-backed artifacts, the digest of the bytes on disk.
    """
    return Artifact(
        artifact_id="",
        type_name=type_name,
        facets={str(k): str(v) for k, v in (facets or {}).items()},
        payload=payload,
        path=path,
        content_hash=content_hash or "",
        producer=producer,
        derived_from=list(derived_from),
        step=step,
        notes=list(notes),
    )


def make_artifact(
    *,
    type_name: str,
    payload: Any = None,
    facets: Optional[Mapping[str, str]] = None,
    producer: str = "",
    derived_from: Iterable[str] = (),
    path: Optional[str] = None,
    step: int = 0,
    notes: Iterable[str] = (),
    content_hash: Optional[str] = None,
    artifact_id: Optional[str] = None,
) -> Artifact:
    """Build a fully-identified artifact in one step (tests and fixtures)."""
    art = draft_artifact(
        type_name=type_name,
        payload=payload,
        facets=facets,
        producer=producer,
        derived_from=derived_from,
        path=path,
        step=step,
        notes=notes,
        content_hash=content_hash,
    ).finalize()
    art.artifact_id = artifact_id or artifact_id_for(type_name, art.content_hash, producer)
    return art


def input_ids(inv: Invocation) -> list[str]:
    """Artifact ids of the invocation inputs, in a deterministic order.

    Sorted by artifact type name because ``inputs`` is a dict and lineage must
    not depend on insertion order.
    """
    return [inv.inputs[key].artifact_id for key in sorted(inv.inputs)]


# ---------------------------------------------------------------------------
# Emptiness
# ---------------------------------------------------------------------------

#: Note attached to an artifact whose entire payload is empty.
EMPTY_PAYLOAD_NOTE = "empty_payload"
#: Prefix for notes naming an empty container *inside* a structured payload.
EMPTY_FIELD_PREFIX = "empty_field:"


def payload_is_empty(payload: Any) -> bool:
    """True when the payload carries no data at all."""
    if payload is None:
        return True
    if isinstance(payload, (str, bytes, bytearray, list, tuple, set, frozenset, dict)):
        return len(payload) == 0
    return False


def empty_field_paths(payload: Any, *, max_depth: int = 3) -> list[str]:
    """Dotted paths to empty containers nested inside ``payload``.

    ``{"status": "ok", "genes": []}`` is the canonical silent failure: the
    envelope looks healthy, the science is gone. Naming ``genes`` here means
    the detector downstream does not have to guess which key mattered.
    Sorted for determinism.
    """
    found: list[str] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, dict):
            for key in sorted(node, key=str):
                child = node[key]
                child_path = f"{prefix}.{key}" if prefix else str(key)
                if payload_is_empty(child):
                    found.append(child_path)
                else:
                    walk(child, child_path, depth + 1)
        elif isinstance(node, (list, tuple)):
            for index, child in enumerate(node):
                child_path = f"{prefix}[{index}]" if prefix else f"[{index}]"
                if isinstance(child, (dict, list, tuple)):
                    walk(child, child_path, depth + 1)

    walk(payload, "", 1)
    return sorted(found)


# ---------------------------------------------------------------------------
# Facet observation
# ---------------------------------------------------------------------------

#: A deterministic payload inspector that reads facets back off real data,
#: e.g. "every identifier matches ``ENSG\\d+`` therefore namespace=ensembl".
#: This is the mechanism that lets a probe *observe* a facet instead of
#: trusting the card. Must be pure and must not consult the network.
FacetObserver = Callable[[Artifact], FacetSet]

#: Prefix for the note recording that an observer contradicted a declaration.
FACET_CONFLICT_PREFIX = "facet_conflict:"


class FinalizedOutputs(NamedTuple):
    outputs: dict[str, Artifact]
    observed_facets: dict[str, FacetSet]
    notes: list[str]


def finalize_outputs(
    outputs: Mapping[str, Artifact],
    *,
    observers: Optional[Mapping[str, FacetObserver]] = None,
    declared_facets: Optional[Mapping[str, Mapping[str, str]]] = None,
    producer: str = "",
    derived_from: Iterable[str] = (),
    step: int = 0,
) -> FinalizedOutputs:
    """Attach provenance, run facet observers, and split observed from declared.

    Precedence on the artifact itself is ``declared < emitted < observed``:
    static configuration is the weakest claim, what the component stamped at
    run time beats it, and what an observer actually read off the payload beats
    both. ``observed_facets`` contains only the latter two, which is why a
    component that emits bare artifacts cannot be credited with the facets its
    card declares.

    A disagreement between a declaration and an observation is not silently
    resolved: it is recorded as a ``facet_conflict:`` note on the artifact and
    returned in ``notes``, because "the card says HGNC and the tool emits
    Ensembl" is exactly the defect this system is meant to catch.
    """
    declared_facets = declared_facets or {}
    observers = observers or {}
    derived = list(derived_from)

    final_outputs: dict[str, Artifact] = {}
    observed: dict[str, FacetSet] = {}
    notes: list[str] = []

    for type_name in sorted(outputs):
        art = outputs[type_name]
        emitted: FacetSet = {str(k): str(v) for k, v in art.facets.items()}
        declared: FacetSet = {str(k): str(v) for k, v in (declared_facets.get(type_name) or {}).items()}

        merged: FacetSet = {**declared, **emitted}
        art_notes = list(art.notes)

        probed: FacetSet = {}
        observer = observers.get(type_name)
        if observer is not None:
            probe_view = art.model_copy(update={"facets": dict(merged)})
            probed = {str(k): str(v) for k, v in (observer(probe_view) or {}).items()}
            for key in sorted(probed):
                prior = merged.get(key)
                if prior is not None and prior != probed[key]:
                    note = (
                        f"{FACET_CONFLICT_PREFIX}{key} "
                        f"declared={prior} observed={probed[key]}"
                    )
                    art_notes.append(note)
                    notes.append(f"{type_name}: {note}")
            merged.update(probed)

        if payload_is_empty(art.payload) and art.path is None:
            if EMPTY_PAYLOAD_NOTE not in art_notes:
                art_notes.append(EMPTY_PAYLOAD_NOTE)
            notes.append(f"{type_name}: emitted an empty payload")
        else:
            for field_path in empty_field_paths(art.payload):
                marker = f"{EMPTY_FIELD_PREFIX}{field_path}"
                if marker not in art_notes:
                    art_notes.append(marker)
                notes.append(f"{type_name}: empty field '{field_path}'")

        updated = art.model_copy(
            update={
                "facets": merged,
                "notes": art_notes,
                "producer": art.producer or producer,
                "derived_from": art.derived_from or derived,
                "step": art.step or step,
            }
        ).finalize()  # no-op when the adapter supplied a byte-level hash
        if not updated.artifact_id:
            updated.artifact_id = artifact_id_for(
                updated.type_name, updated.content_hash, updated.producer
            )

        final_outputs[type_name] = updated
        # Observation is emitted-at-runtime plus observer-confirmed. Declared
        # facets deliberately do not appear.
        seen = {**emitted, **probed}
        if seen:
            observed[type_name] = seen

    return FinalizedOutputs(final_outputs, observed, notes)


# ---------------------------------------------------------------------------
# Templating (shared by subprocess, container, coding-agent, and LLM adapters)
# ---------------------------------------------------------------------------


class TemplateError(ValueError):
    """An argv or prompt template referenced something that does not exist."""


_PLACEHOLDER = re.compile(
    r"\{\{|\}\}|\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^{}]*))?\}"
)


def render_template(template: str, resolver: Callable[[str, Optional[str]], str]) -> str:
    """Substitute ``{kind}`` / ``{kind:arg}`` placeholders. ``{{`` escapes a brace.

    Deliberately not :meth:`str.format`: an unknown placeholder must raise a
    named error that becomes a CONFIGURATION-classed fault, not a KeyError
    buried in a traceback or, worse, silently formatted into the command line.
    """
    def substitute(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        return resolver(match.group(1), match.group(2))

    return _PLACEHOLDER.sub(substitute, template)


def stringify(value: Any) -> str:
    """Deterministic string form of a config value or payload."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def standard_resolver(
    inv: Invocation,
    *,
    workdir: Optional[str] = None,
    input_paths: Optional[Mapping[str, str]] = None,
    output_paths: Optional[Mapping[str, str]] = None,
) -> Callable[[str, Optional[str]], str]:
    """Placeholder resolver shared by every command/prompt template.

    ``{in:T}``      payload of input artifact of type ``T``, as canonical JSON
    ``{inpath:T}``  filesystem path of that input
    ``{out:T}``     filesystem path the component must write output ``T`` to
    ``{cfg:K}``     config value ``K``
    ``{facet:T.K}`` facet ``K`` declared on input artifact ``T``
    ``{seed}`` ``{workdir}`` ``{component}`` ``{subgoal}``
    """
    input_paths = input_paths or {}
    output_paths = output_paths or {}

    def resolve(kind: str, arg: Optional[str]) -> str:
        if arg is None:
            if kind == "seed":
                return str(inv.seed)
            if kind == "component":
                return inv.component
            if kind == "subgoal":
                return inv.subgoal_id or ""
            if kind == "workdir":
                if workdir is None:
                    raise TemplateError("template uses {workdir} but no workdir was provided")
                return workdir
            raise TemplateError(f"unknown template placeholder '{{{kind}}}'")

        if kind == "cfg":
            if arg not in inv.config:
                raise TemplateError(f"template references missing config key '{arg}'")
            return stringify(inv.config[arg])
        if kind == "in":
            if arg not in inv.inputs:
                raise TemplateError(f"template references missing input artifact '{arg}'")
            return stringify(inv.inputs[arg].payload)
        if kind == "inpath":
            if arg not in input_paths:
                raise TemplateError(f"template references unmaterialized input path '{arg}'")
            return input_paths[arg]
        if kind == "out":
            if arg not in output_paths:
                raise TemplateError(f"template references undeclared output '{arg}'")
            return output_paths[arg]
        if kind == "facet":
            type_name, _, facet_key = arg.partition(".")
            art = inv.inputs.get(type_name)
            if art is None or facet_key not in art.facets:
                raise TemplateError(f"template references undeclared facet '{arg}'")
            return art.facets[facet_key]
        raise TemplateError(f"unknown template placeholder '{{{kind}:{arg}}}'")

    return resolve


def render_argv(
    argv: Iterable[str],
    inv: Invocation,
    *,
    workdir: Optional[str] = None,
    input_paths: Optional[Mapping[str, str]] = None,
    output_paths: Optional[Mapping[str, str]] = None,
) -> list[str]:
    """Render every element of an argv template. Never goes through a shell."""
    resolver = standard_resolver(
        inv, workdir=workdir, input_paths=input_paths, output_paths=output_paths
    )
    return [render_template(token, resolver) for token in argv]


def stable_digest(*parts: str, length: int = 16) -> str:
    """SHA-256 over unit-separated parts. Used for reproducible synthetic ids."""
    blob = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:length]


def file_digest(data: bytes, length: int = 16) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def ensure_workdir(inv: Invocation, *, required_by: str) -> Path:
    """Resolve the invocation workdir, creating it, or raise a clear error."""
    if inv.workdir is None:
        raise FileNotFoundError(f"{required_by} requires Invocation.workdir to be set")
    path = Path(inv.workdir)
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "Clock",
    "perf_clock",
    "zero_clock",
    "error_line",
    "parse_error_line",
    "errors_of_class",
    "Invocation",
    "InvocationResult",
    "ComponentAdapter",
    "DeclaresBehavior",
    "behavior_of",
    "AdapterRegistry",
    "failure_result",
    "artifact_id_for",
    "draft_artifact",
    "make_artifact",
    "input_ids",
    "EMPTY_PAYLOAD_NOTE",
    "EMPTY_FIELD_PREFIX",
    "payload_is_empty",
    "empty_field_paths",
    "FacetObserver",
    "FACET_CONFLICT_PREFIX",
    "FinalizedOutputs",
    "finalize_outputs",
    "TemplateError",
    "render_template",
    "stringify",
    "standard_resolver",
    "render_argv",
    "stable_digest",
    "file_digest",
    "ensure_workdir",
]
