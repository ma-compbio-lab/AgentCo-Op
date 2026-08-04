"""Execute a registered facet converter and measure what it actually converted.

The compiler is allowed to bridge a facet mismatch — Ensembl to HGNC, mouse to
human orthologs, counts to CPM — only by inserting a converter that exists as
executable code. This adapter is what runs it, and its job is not merely to
call the function: it is to answer the question the v1 system never asked,
namely *how much of the data survived*.

An identifier mapping that resolves 60% of genes is not a successful
conversion. It is a 40% silent data loss that the downstream enrichment step
will happily consume, producing a result that is wrong in a way no schema check
can see. So :class:`ConverterAdapter` counts entries in and entries out and
fails the invocation when realized coverage falls below the converter's
declared ``expected_coverage``.

Two refusals are as important as the measurement:

* If coverage cannot be counted, the adapter reports it as **unmeasured**. It
  never records 1.0. An uncounted coverage is not a full coverage, and writing
  1.0 would let the ``coverage_threshold`` check pass on nothing.
* If the input does not *declare* the facet the converter is defined to consume,
  the adapter refuses to run rather than assuming the converter applies. That
  is the ``UNDERSPECIFIED`` verdict from :mod:`agentcoop.ir.artifacts`, enforced
  at run time: we cannot prove these identifiers are Ensembl, so we will not
  map them as though they were.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict

from agentcoop.ir.artifacts import Artifact, Converter, FacetSet
from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.faults import FaultClass

from agentcoop.components.base import (
    Clock,
    FacetObserver,
    Invocation,
    InvocationResult,
    draft_artifact,
    error_line,
    failure_result,
    finalize_outputs,
    perf_clock,
)

#: Prefix of the artifact note carrying the JSON-encoded coverage report.
COVERAGE_NOTE_PREFIX = "coverage="


class CoverageReport(BaseModel):
    """What the conversion actually did to the data."""

    model_config = ConfigDict(extra="forbid")

    converter: str
    #: False when entries could not be counted. ``realized`` is then ``None``
    #: and no coverage claim may be made in either direction.
    measurable: bool
    entries_in: Optional[int] = None
    entries_out: Optional[int] = None
    realized: Optional[float] = None
    expected: float = 1.0
    #: The payload key entries were counted under, and whether it was inferred.
    coverage_key: Optional[str] = None
    inferred_key: bool = False
    detail: str = ""

    @property
    def shortfall(self) -> bool:
        """True only when a measured coverage is below what was declared."""
        return self.realized is not None and self.realized < self.expected - 1e-9

    def as_note(self) -> str:
        return COVERAGE_NOTE_PREFIX + json.dumps(
            self.model_dump(mode="json"), sort_keys=True
        )


def coverage_from_artifact(artifact: Artifact) -> Optional[CoverageReport]:
    """Read a coverage report back off an artifact's notes."""
    for note in artifact.notes:
        if note.startswith(COVERAGE_NOTE_PREFIX):
            try:
                return CoverageReport.model_validate_json(note[len(COVERAGE_NOTE_PREFIX):])
            except Exception:  # noqa: BLE001 - a corrupt note is simply absent
                return None
    return None


def count_entries(payload: Any, coverage_key: Optional[str] = None) -> tuple[Optional[int], Optional[str], bool]:
    """Count convertible entries in a payload.

    Returns ``(count, key_used, inferred)``. Returns ``None`` rather than a
    guess whenever the payload shape does not make the count unambiguous —
    the whole point is that an unmeasurable coverage must stay unmeasurable.

    When no key is given and the payload is a mapping with exactly one
    container-valued field, that field is used and the inference is recorded so
    a reader can see the count was not directly specified.
    """
    if coverage_key is not None:
        if isinstance(payload, Mapping) and coverage_key in payload:
            value = payload[coverage_key]
            if isinstance(value, (list, tuple, set, frozenset, dict)):
                return len(value), coverage_key, False
        return None, coverage_key, False

    if isinstance(payload, (list, tuple, set, frozenset)):
        return len(payload), None, False
    if isinstance(payload, Mapping):
        container_keys = sorted(
            str(k)
            for k, v in payload.items()
            if isinstance(v, (list, tuple, set, frozenset, dict))
        )
        if len(container_keys) == 1:
            return len(payload[container_keys[0]]), container_keys[0], True
        if not container_keys:
            # A flat mapping is itself the entry collection (id -> value).
            return len(payload), None, False
        return None, None, False
    return None, None, False


def realized_coverage(
    before: Any, after: Any, *, coverage_key: Optional[str] = None
) -> tuple[Optional[float], Optional[int], Optional[int], Optional[str], bool]:
    """Fraction of entries that survived a conversion.

    Exported so the ``coverage_threshold`` check in :mod:`agentcoop.evaluate`
    measures coverage the same way the adapter did; two implementations of this
    would eventually disagree and the disagreement would look like a bug in the
    converter.
    """
    n_in, key_in, inferred_in = count_entries(before, coverage_key)
    n_out, key_out, inferred_out = count_entries(after, coverage_key)
    key = key_in or key_out
    inferred = inferred_in or inferred_out
    if n_in is None or n_out is None:
        return None, n_in, n_out, key, inferred
    if n_in == 0:
        return None, n_in, n_out, key, inferred
    return n_out / n_in, n_in, n_out, key, inferred


class ConverterAdapter:
    """Run an :class:`agentcoop.ir.artifacts.Converter` as a workflow node."""

    def __init__(
        self,
        converter: Converter,
        *,
        name: Optional[str] = None,
        coverage_key: Optional[str] = None,
        strict_input_facets: bool = True,
        facet_observers: Optional[Mapping[str, FacetObserver]] = None,
        clock: Clock = perf_clock,
    ) -> None:
        """``strict_input_facets=False`` downgrades the underspecified-input
        refusal to a warning. Provided for exploratory work only; leaving it on
        is what makes an inserted adapter defensible."""
        self.converter = converter
        self.name = name or f"convert:{converter.name}"
        self.coverage_key = coverage_key
        self.strict_input_facets = strict_input_facets
        self._facet_observers = dict(facet_observers or {})
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        """A converter is a pure function of its input, by construction."""
        return BehaviorContract(
            deterministic=True, idempotent=True, retry_safe=True, shadow_safe=True
        )

    def input_facet_problems(self, artifact: Artifact) -> list[str]:
        """Reasons this converter cannot be proven to apply to ``artifact``.

        Missing and conflicting are reported differently because they demand
        different repairs: a missing declaration needs the producer probed or
        the contract tightened, a conflicting one needs a different converter.
        """
        problems: list[str] = []
        for key, expected in sorted(self.converter.from_facets.items()):
            have = artifact.facets.get(key)
            if have is None:
                problems.append(
                    f"input does not declare facet '{key}' that converter "
                    f"'{self.converter.name}' consumes (expects '{expected}'); "
                    "underspecified is not compatible — probe the producer or "
                    "declare the facet instead of assuming it"
                )
            elif have != expected:
                problems.append(
                    f"input declares {key}='{have}' but converter "
                    f"'{self.converter.name}' consumes {key}='{expected}'"
                )
        return problems

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        type_name = self.converter.type_name
        source = inv.inputs.get(type_name)
        if source is None:
            return failure_result(
                FaultClass.ARTIFACT_CONTRACT,
                f"converter '{self.converter.name}' requires an input artifact of type "
                f"'{type_name}'; got {sorted(inv.inputs) or 'no inputs'}",
                cost=CostProfile(latency_s=self._clock() - started),
            )

        if self.converter.fn is None:
            return failure_result(
                FaultClass.CAPABILITY_MISMATCH,
                f"converter '{self.converter.name}' is registered but has no executable "
                "implementation; a facet mismatch cannot be bridged by a declaration",
                cost=CostProfile(latency_s=self._clock() - started),
            )

        facet_problems = self.input_facet_problems(source)
        if facet_problems and self.strict_input_facets:
            return failure_result(
                FaultClass.ARTIFACT_CONTRACT,
                "; ".join(facet_problems),
                cost=CostProfile(latency_s=self._clock() - started),
            )

        try:
            converted = self.converter.fn(source)
        except Exception as exc:  # noqa: BLE001
            return failure_result(
                FaultClass.TOOL_FAILURE,
                f"converter '{self.converter.name}' raised {type(exc).__name__}: {exc}",
                cost=CostProfile(latency_s=self._clock() - started),
                exit_code=1,
            )

        if not isinstance(converted, Artifact):
            converted = draft_artifact(type_name=type_name, payload=converted)

        realized, n_in, n_out, key, inferred = realized_coverage(
            source.payload, converted.payload, coverage_key=self.coverage_key
        )
        report = CoverageReport(
            converter=self.converter.name,
            measurable=realized is not None,
            entries_in=n_in,
            entries_out=n_out,
            realized=realized,
            expected=self.converter.expected_coverage,
            coverage_key=key,
            inferred_key=inferred,
            detail=(
                f"{n_out}/{n_in} entries survived conversion"
                if realized is not None
                else "entry counts are not derivable from this payload shape; "
                "coverage is UNMEASURED, which is not the same as complete"
            ),
        )

        # ``to_facets`` is what the converter is *defined* to produce, so it is
        # applied as a declaration. Only facets the conversion function itself
        # stamped on the returned artifact — or that an observer read back off
        # the converted payload — count as observed.
        declared: dict[str, FacetSet] = {
            type_name: {
                **{k: v for k, v in source.facets.items() if k not in self.converter.from_facets},
                **self.converter.to_facets,
            }
        }
        notes = [*converted.notes, report.as_note()]
        if facet_problems and not self.strict_input_facets:
            notes.extend(f"underspecified_input:{p}" for p in facet_problems)

        staged = converted.model_copy(
            update={"notes": notes, "derived_from": converted.derived_from or [source.artifact_id]}
        )
        finalized = finalize_outputs(
            {type_name: staged},
            observers=self._facet_observers,
            declared_facets=declared,
            producer=inv.producer_id,
            derived_from=[source.artifact_id],
        )

        errors: list[str] = []
        logs: list[str] = [report.detail, *finalized.notes]
        if report.shortfall:
            errors.append(
                error_line(
                    FaultClass.ARTIFACT_CONTRACT,
                    f"lossy conversion: '{self.converter.name}' realized coverage "
                    f"{report.realized:.3f} against a declared expectation of "
                    f"{report.expected:.3f} ({n_out}/{n_in} entries); the mapping "
                    "dropped data that downstream steps would otherwise treat as absent",
                )
            )
        elif not report.measurable:
            logs.append(
                f"coverage for '{self.converter.name}' is UNMEASURED "
                "(no coverage claim recorded)"
            )
        if facet_problems and not self.strict_input_facets:
            logs.extend(facet_problems)

        return InvocationResult(
            ok=not errors,
            outputs=finalized.outputs,
            cost=CostProfile(latency_s=self._clock() - started),
            logs="\n".join(line for line in logs if line),
            errors=errors,
            exit_code=0 if not errors else 1,
            observed_facets=finalized.observed_facets,
        )


__all__ = [
    "COVERAGE_NOTE_PREFIX",
    "CoverageReport",
    "coverage_from_artifact",
    "count_entries",
    "realized_coverage",
    "ConverterAdapter",
]
