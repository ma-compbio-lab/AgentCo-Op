"""Executing probes, and turning a card's declarations into earned certification.

Everything in this module is deliberately conservative about what an execution
can be taken to prove:

* A probe that could not reach a verdict **fails**. There is no "we could not
  check, so assume it works" path, because that is precisely how a component
  that rejects every input reaches CERTIFIED in a system that only counts
  crashes.
* Facets observed on emitted artifacts are promoted into the card **whether or
  not the probe passed**. If the manifest says HGNC and the tool emits Ensembl,
  the schema probe fails *and* the card is corrected — suppressing the
  observation because the probe failed would discard the only evidence that
  explains the failure.
* Determinism that could not be demonstrated is recorded as non-determinism.
* A component that returns ``ok=True`` on garbage earns a
  ``FailureSignature(silent=True)``, which the certification ladder treats as
  disqualifying.

``certify`` never mutates the card it is given. Certification produces a new
card so that a caller holding the declared version can diff it against the
earned one, which is what makes "the README was wrong about this" a reportable
finding rather than an invisible correction.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, NamedTuple, Optional

from agentcoop.components.base import (
    EMPTY_FIELD_PREFIX,
    EMPTY_PAYLOAD_NOTE,
    AdapterRegistry,
    Clock,
    Invocation,
    InvocationResult,
    empty_field_paths,
    payload_is_empty,
    perf_clock,
)
from agentcoop.ir.artifacts import (
    Artifact,
    ArtifactType,
    FacetSet,
    TypeRegistry,
    validate_payload,
)
from agentcoop.ir.capability import (
    CapabilityCard,
    CostProfile,
    EmpiricalRecord,
    FailureSignature,
    ProbeOutcome,
)
from agentcoop.ir.dossier import ResourceLimits
from agentcoop.ir.faults import FaultClass
from agentcoop.probe.spec import ProbeSpec
from agentcoop.probe.suite import (
    _parameter_value_hash,
    resolve_type,
    standard_suite,
)

#: Faults that mean the component was never actually reached, as opposed to
#: reached and unhappy. Only these can fail the ``reachable`` probe.
UNREACHABLE_FAULTS = frozenset({FaultClass.ENVIRONMENT, FaultClass.CONFIGURATION})


class _Attempt(NamedTuple):
    """One invocation, plus how it ended.

    ``timed_out`` is tracked separately from ``result.ok`` because the resource
    probe has to distinguish "the component noticed the budget and stopped"
    from "the runner killed it". Both produce a failed result; only the first
    is honouring a limit.
    """

    result: InvocationResult
    timed_out: bool = False
    raised: Optional[BaseException] = None
    duration_s: float = 0.0

    @property
    def reached(self) -> bool:
        return self.raised is None


class ProbeRunner:
    def __init__(
        self,
        adapters: AdapterRegistry,
        *,
        registry: Optional[TypeRegistry] = None,
        clock: Clock = perf_clock,
    ) -> None:
        self.adapters = adapters
        self.registry = registry
        self.clock = clock

    # -- invocation --------------------------------------------------------

    async def _invoke(
        self,
        card: CapabilityCard,
        spec: ProbeSpec,
        *,
        limits: Optional[ResourceLimits] = None,
        seed: Optional[int] = None,
    ) -> _Attempt:
        started = self.clock()
        try:
            adapter = self.adapters.get(card.name)
        except KeyError as exc:
            return _Attempt(
                result=InvocationResult(ok=False, errors=[str(exc)]),
                raised=exc,
                duration_s=self.clock() - started,
            )

        inv = Invocation(
            component=card.name,
            subgoal_id=spec.subgoal_id,
            inputs=dict(spec.inputs),
            config={**spec.config, "node_id": spec.probe_id},
            limits=limits,
            seed=spec.seed if seed is None else seed,
        )
        try:
            result = await asyncio.wait_for(adapter.invoke(inv), timeout=spec.timeout_s)
        except asyncio.TimeoutError:
            return _Attempt(
                result=InvocationResult(
                    ok=False, errors=[f"probe timed out after {spec.timeout_s}s"]
                ),
                timed_out=True,
                duration_s=self.clock() - started,
            )
        except Exception as exc:  # adapter blew up rather than reporting failure
            return _Attempt(
                result=InvocationResult(
                    ok=False, errors=[f"{type(exc).__name__}: {exc}"]
                ),
                raised=exc,
                duration_s=self.clock() - started,
            )
        return _Attempt(result=result, duration_s=self.clock() - started)

    # -- dispatch ----------------------------------------------------------

    async def run_probe(self, card: CapabilityCard, spec: ProbeSpec) -> ProbeOutcome:
        handler = {
            "reachable": self._reachable,
            "schema": self._schema,
            "smoke": self._smoke,
            "invalid_input": self._invalid_input,
            "determinism": self._determinism,
            "resource": self._resource,
        }.get(spec.kind)
        if handler is None:  # pragma: no cover - Literal makes this unreachable
            return ProbeOutcome(
                probe_id=spec.probe_id,
                kind=spec.kind,
                passed=False,
                detail=f"no handler for probe kind '{spec.kind}'",
            )
        return await handler(card, spec)

    async def run_suite(
        self, card: CapabilityCard, specs: Iterable[ProbeSpec]
    ) -> list[ProbeOutcome]:
        """Run probes in the given order. Sequential on purpose.

        Concurrency here would let one probe's resource pressure change
        another's verdict, which would make certification depend on scheduling.
        """
        return [await self.run_probe(card, spec) for spec in specs]

    # -- individual probes -------------------------------------------------

    async def _reachable(self, card: CapabilityCard, spec: ProbeSpec) -> ProbeOutcome:
        attempt = await self._invoke(card, spec)
        faults = set(attempt.result.fault_classes())
        blocking = sorted(f.value for f in faults & UNREACHABLE_FAULTS)

        if not attempt.reached:
            detail = f"the adapter could not be invoked: {'; '.join(attempt.result.errors)}"
        elif attempt.timed_out:
            detail = f"no response within {spec.timeout_s}s"
        elif blocking:
            detail = (
                "the entrypoint could not be resolved in this environment "
                f"({', '.join(blocking)}): {'; '.join(attempt.result.errors[:2])}"
            )
        else:
            detail = "the entrypoint is reachable and returned a result"

        passed = attempt.reached and not attempt.timed_out and not blocking
        return self._outcome(
            spec,
            attempt,
            passed=passed,
            detail=detail,
            evidence={"blocking_faults": blocking},
        )

    async def _schema(self, card: CapabilityCard, spec: ProbeSpec) -> ProbeOutcome:
        attempt = await self._invoke(card, spec)
        expected: list[str] = list(spec.expectations.get("produces") or [])
        outputs = attempt.result.outputs

        if not attempt.result.ok:
            return self._outcome(
                spec,
                attempt,
                passed=False,
                detail=(
                    "the component rejected the input synthesized from its own "
                    "declared contract, so the output schema could not be "
                    f"confirmed: {'; '.join(attempt.result.errors[:2]) or 'no error reported'}"
                ),
            )

        problems: list[str] = []
        for type_name in expected:
            art = outputs.get(type_name)
            if art is None:
                problems.append(f"declared output '{type_name}' was not emitted")
                continue
            declared = card.io.produced_type(type_name)
            if declared is None:
                continue
            resolved = resolve_type(declared, self.registry)
            for issue in validate_payload(art.payload, resolved):
                problems.append(f"{type_name}: {issue}")
            problems.extend(_facet_problems(art, resolved))

        undeclared = sorted(set(outputs) - set(expected))
        return self._outcome(
            spec,
            attempt,
            passed=not problems,
            detail=(
                "; ".join(problems)
                if problems
                else f"emitted {len(expected)} declared output(s), all structurally valid"
            ),
            evidence={"problems": problems, "undeclared_outputs": undeclared},
        )

    async def _smoke(self, card: CapabilityCard, spec: ProbeSpec) -> ProbeOutcome:
        attempt = await self._invoke(card, spec)
        expected: list[str] = list(spec.expectations.get("produces") or [])
        deep = spec.expectations.get("input_source") == "declared"
        outputs = attempt.result.outputs

        if not attempt.result.ok:
            return self._outcome(
                spec,
                attempt,
                passed=False,
                detail=(
                    "the component failed on a representative input: "
                    f"{'; '.join(attempt.result.errors[:2]) or 'no error reported'}"
                ),
            )

        problems: list[str] = []
        for type_name in expected:
            art = outputs.get(type_name)
            if art is None:
                problems.append(f"'{type_name}' was not emitted")
                continue
            if payload_is_empty(art.payload) and art.path is None:
                problems.append(f"'{type_name}' carries an empty payload")
                continue
            if deep:
                # Derived from the payload rather than read off the adapter's
                # notes. An adapter that forgets to annotate its own emptiness
                # is exactly the one this probe has to catch, so trusting the
                # annotation would make the check self-defeating; notes are
                # merged in as a supplement, not relied on.
                empty_fields = set(empty_field_paths(art.payload)) | {
                    n[len(EMPTY_FIELD_PREFIX) :]
                    for n in art.notes
                    if n.startswith(EMPTY_FIELD_PREFIX)
                }
                if empty_fields:
                    problems.append(
                        f"'{type_name}' is structurally present but empty where it "
                        f"matters: {', '.join(sorted(empty_fields))}"
                    )

        return self._outcome(
            spec,
            attempt,
            passed=not problems,
            detail=(
                "; ".join(problems)
                if problems
                else "produced a non-vacuous result on a "
                + str(spec.expectations.get("input_source", "synthetic"))
                + " input"
            ),
            evidence={"problems": problems, "deep_emptiness_checked": deep},
        )

    async def _invalid_input(
        self, card: CapabilityCard, spec: ProbeSpec
    ) -> ProbeOutcome:
        """The probe that decides whether a component may ever be CERTIFIED.

        Passing means the component said *no*. Anything else — a plausible
        answer, an empty answer, or no answer at all while reporting success —
        is a silent failure, and each variety is recorded distinctly because
        they need different repairs.
        """
        attempt = await self._invoke(card, spec)
        defect = spec.expectations.get("defect", "invalid input")

        if attempt.raised is not None:
            return self._outcome(
                spec,
                attempt,
                passed=False,
                detail=(
                    f"could not establish refusal: the adapter itself failed "
                    f"({attempt.result.errors[0] if attempt.result.errors else attempt.raised})"
                ),
                evidence={"silent": False, "inconclusive": True},
            )

        if not attempt.result.ok:
            return self._outcome(
                spec,
                attempt,
                passed=True,
                detail=f"correctly refused {defect}",
                evidence={"silent": False},
            )

        # ok=True on garbage. Which flavour of silence?
        outputs = attempt.result.outputs
        if not outputs:
            mode = "no_output"
            described = "reported success while emitting nothing at all"
        elif all(
            payload_is_empty(a.payload) and a.path is None for a in outputs.values()
        ) or all(EMPTY_PAYLOAD_NOTE in a.notes for a in outputs.values()):
            mode = "empty_output"
            described = "reported success while emitting an empty result"
        else:
            mode = "plausible_output"
            described = (
                "reported success and emitted a plausible-looking result, which "
                "downstream nodes have no way to distinguish from a real one"
            )

        return self._outcome(
            spec,
            attempt,
            passed=False,
            detail=f"accepted {defect}: it {described}",
            evidence={"silent": True, "silent_mode": mode, "defect": defect},
        )

    async def _determinism(
        self, card: CapabilityCard, spec: ProbeSpec
    ) -> ProbeOutcome:
        repeat = int(spec.expectations.get("repeat", 2) or 2)
        attempts = [
            await self._invoke(card, spec, seed=spec.seed) for _ in range(max(2, repeat))
        ]

        if not all(a.result.ok for a in attempts):
            return self._outcome(
                spec,
                attempts[0],
                passed=False,
                detail=(
                    "determinism could not be demonstrated: at least one run of the "
                    "identical invocation failed, so the component is recorded as "
                    "non-deterministic rather than assumed otherwise"
                ),
                evidence={"inconclusive": True},
            )

        signatures = [
            {name: art.content_hash for name, art in sorted(a.result.outputs.items())}
            for a in attempts
        ]
        first = signatures[0]
        differing = sorted(
            {
                name
                for sig in signatures[1:]
                for name in set(first) | set(sig)
                if first.get(name) != sig.get(name)
            }
        )
        return self._outcome(
            spec,
            attempts[0],
            passed=not differing,
            detail=(
                f"identical seed produced identical content hashes across {len(attempts)} runs"
                if not differing
                else "identical seed produced different content for: "
                + ", ".join(differing)
            ),
            evidence={"signatures": signatures, "differing_outputs": differing},
        )

    async def _resource(self, card: CapabilityCard, spec: ProbeSpec) -> ProbeOutcome:
        declared = spec.expectations.get("limits") or {}
        limits = ResourceLimits(
            max_wall_time_s=declared.get("max_wall_time_s"),
            max_usd=declared.get("max_usd"),
            max_tokens=declared.get("max_tokens"),
        )
        attempt = await self._invoke(card, spec, limits=limits)
        cost = attempt.result.cost

        if attempt.timed_out:
            return self._outcome(
                spec,
                attempt,
                passed=False,
                detail=(
                    "the component did not honour the stated budget; the runner had "
                    "to terminate it, which means the limit is not enforceable from "
                    "inside the component"
                ),
                evidence={"terminated_by_runner": True},
            )

        if not attempt.result.ok:
            return self._outcome(
                spec,
                attempt,
                passed=True,
                detail="failed cleanly rather than exceeding the stated budget",
                evidence={"clean_refusal": True},
            )

        overruns = _budget_overruns(cost, limits)
        return self._outcome(
            spec,
            attempt,
            passed=not overruns,
            detail=(
                "completed inside the stated budget"
                if not overruns
                else "reported success while exceeding the stated budget: "
                + "; ".join(overruns)
            ),
            evidence={"overruns": overruns},
        )

    # -- outcome construction ---------------------------------------------

    def _outcome(
        self,
        spec: ProbeSpec,
        attempt: _Attempt,
        *,
        passed: bool,
        detail: str,
        evidence: Optional[dict[str, Any]] = None,
    ) -> ProbeOutcome:
        observed = {
            type_name: dict(facets)
            for type_name, facets in attempt.result.observed_facets.items()
            if facets
        }
        payload: dict[str, Any] = dict(evidence or {})
        payload.setdefault("errors", list(attempt.result.errors[:4]))
        payload["cost"] = attempt.result.cost.model_dump()
        payload["description"] = spec.description
        if spec.expectations.get("probe_scope") == "parameter_domain":
            metadata_keys = (
                "probe_scope",
                "parameter",
                "allowed_values_hash",
                "value_hash",
                "contract_hash",
                "output_context_id",
            )
            metadata_valid = all(key in spec.expectations for key in metadata_keys)
            parameter = spec.expectations.get("parameter")
            if metadata_valid and isinstance(parameter, str) and parameter in spec.config:
                try:
                    metadata_valid = (
                        _parameter_value_hash(spec.config[parameter])
                        == spec.expectations["value_hash"]
                    )
                except (TypeError, ValueError, UnicodeError):
                    metadata_valid = False
            else:
                metadata_valid = False
            for key in metadata_keys:
                if key in spec.expectations:
                    payload[key] = spec.expectations[key]
            payload["probe_metadata_valid"] = metadata_valid
            payload["contract_preserving"] = bool(passed and metadata_valid)
        return ProbeOutcome(
            probe_id=spec.probe_id,
            kind=spec.kind,
            passed=passed,
            duration_s=attempt.duration_s,
            detail=detail,
            observed_facets=observed,
            evidence=payload,
        )

    # -- certification -----------------------------------------------------

    async def certify(
        self,
        card: CapabilityCard,
        specs: Optional[list[ProbeSpec]] = None,
    ) -> CapabilityCard:
        """Return a new card whose empirical half reflects what actually ran."""
        specs = specs if specs is not None else standard_suite(card, registry=self.registry)
        outcomes = await self.run_suite(card, specs)
        return apply_outcomes(card, outcomes)


# ---------------------------------------------------------------------------
# Folding outcomes back into a card
# ---------------------------------------------------------------------------


def apply_outcomes(
    card: CapabilityCard, outcomes: list[ProbeOutcome]
) -> CapabilityCard:
    """Merge probe outcomes into a copy of ``card``.

    Merging is by ``probe_id``, not by kind: re-running a single corruption
    updates that one verdict and leaves the rest of the evidence standing,
    which is what makes incremental re-certification meaningful.
    """
    merged: dict[str, ProbeOutcome] = {p.probe_id: p for p in card.empirical.probes}
    for outcome in outcomes:
        merged[outcome.probe_id] = outcome
    probes = sorted(merged.values(), key=lambda p: (p.kind, p.probe_id))

    confirmed: dict[str, FacetSet] = {
        k: dict(v) for k, v in card.empirical.confirmed_facets.items()
    }
    for outcome in outcomes:
        for type_name, facets in outcome.observed_facets.items():
            confirmed.setdefault(type_name, {}).update(facets)

    empirical = EmpiricalRecord(
        probes=probes,
        reliability=card.empirical.reliability,
        observed_cost=_merge_cost(card.empirical.observed_cost, outcomes),
        confirmed_facets=confirmed,
        last_probed_commit=card.empirical.last_probed_commit,
    )

    behavior = card.behavior
    determinism = [o for o in outcomes if o.kind == "determinism"]
    if determinism:
        behavior = behavior.model_copy(
            update={"deterministic": all(o.passed for o in determinism)}
        )

    return card.model_copy(
        update={
            "empirical": empirical,
            "behavior": behavior,
            "failure_profile": _merge_failure_profile(card, outcomes),
        },
        deep=True,
    )


def _merge_failure_profile(
    card: CapabilityCard, outcomes: list[ProbeOutcome]
) -> list[FailureSignature]:
    signatures = {sig.name: sig for sig in card.failure_profile}
    for outcome in outcomes:
        if outcome.kind != "invalid_input" or outcome.passed:
            continue
        if not outcome.evidence.get("silent"):
            continue
        mode = str(outcome.evidence.get("silent_mode", "silent"))
        name = f"silent_{mode}"
        signatures[name] = FailureSignature(
            name=name,
            fault_class=FaultClass.ARTIFACT_CONTRACT,
            silent=True,
            note=outcome.detail,
        )
    return [signatures[k] for k in sorted(signatures)]


def _merge_cost(
    prior: Optional[CostProfile], outcomes: list[ProbeOutcome]
) -> Optional[CostProfile]:
    """Elementwise maximum over observed probe costs.

    The maximum rather than the mean: this number is used for budgeting, and
    under-provisioning a workflow because half the probes were cheap is a
    failure mode the compiler cannot recover from at run time.
    """
    costs = [prior] if prior is not None else []
    for outcome in outcomes:
        raw = outcome.evidence.get("cost")
        if isinstance(raw, dict):
            try:
                costs.append(CostProfile(**raw))
            except (TypeError, ValueError):
                continue
    if not costs:
        return None
    return CostProfile(
        latency_s=max(c.latency_s for c in costs),
        tokens=max(c.tokens for c in costs),
        usd=max(c.usd for c in costs),
        cpu_seconds=max(c.cpu_seconds for c in costs),
        gpu_seconds=max(c.gpu_seconds for c in costs),
        peak_memory_gb=max(c.peak_memory_gb for c in costs),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facet_problems(artifact: Artifact, declared: ArtifactType) -> list[str]:
    """Facets the type requires that the emitted artifact does not carry."""
    problems: list[str] = []
    for key in declared.required_facets:
        if key not in artifact.facets:
            problems.append(
                f"{declared.name}: required facet '{key}' is absent from the emitted "
                "artifact, so any downstream edge is underspecified"
            )
            continue
        expected = declared.facets.get(key)
        actual = artifact.facets.get(key)
        if expected is not None and actual != expected:
            problems.append(
                f"{declared.name}: facet '{key}' was declared '{expected}' but "
                f"'{actual}' was emitted"
            )
    return problems


def _budget_overruns(cost: CostProfile, limits: ResourceLimits) -> list[str]:
    overruns: list[str] = []
    if limits.max_wall_time_s and cost.latency_s > limits.max_wall_time_s:
        overruns.append(f"latency {cost.latency_s:.2f}s > {limits.max_wall_time_s:.2f}s")
    if limits.max_usd and cost.usd > limits.max_usd:
        overruns.append(f"cost ${cost.usd:.4f} > ${limits.max_usd:.4f}")
    if limits.max_tokens and cost.tokens > limits.max_tokens:
        overruns.append(f"tokens {cost.tokens} > {limits.max_tokens}")
    return overruns


__all__ = [
    "ProbeRunner",
    "apply_outcomes",
    "UNREACHABLE_FAULTS",
]
