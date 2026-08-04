"""Adapter around an in-process Python callable.

This is the deterministic workhorse: built-in analysis steps, merge functions,
converters used as workflow nodes, and almost every unit test run through it.
Because it is the reference implementation of "deterministic component", it
takes determinism seriously rather than merely asserting it:

* ``inv.seed`` is handed to the wrapped callable *and* used to seed the global
  :mod:`random` module for the duration of the call, with the previous state
  restored afterwards. A component that reaches for module-level randomness
  therefore still reproduces under a fixed seed, and one that reaches for
  entropy the seed cannot reach (``os.urandom``, wall clock) still shows up as
  non-deterministic under the determinism probe instead of being papered over.
* Artifact identity is content-addressed after facet observation, so two runs
  with the same seed yield the same ``content_hash``.

The callable may return an :class:`InvocationResult`, a mapping of artifacts,
a single :class:`Artifact`, or a bare payload. The bare-payload form exists so
that a plain analysis function can be lifted into the workflow without being
rewritten, but it is exactly the form where the adapter has to *stamp* facets
from configuration — and stamped facets never enter ``observed_facets``.
"""

from __future__ import annotations

import inspect
import random
from typing import Any, Awaitable, Callable, Mapping, Optional, Union

from agentcoop.ir.artifacts import Artifact
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
    input_ids,
    perf_clock,
)

#: Anything the wrapped callable may return. See module docstring.
PythonFnReturn = Union[InvocationResult, Mapping[str, Artifact], Artifact, Any]
PythonFn = Callable[[Invocation], Union[PythonFnReturn, Awaitable[PythonFnReturn]]]


class PythonFunctionAdapter:
    """Invoke a Python callable as a workflow component."""

    def __init__(
        self,
        name: str,
        fn: PythonFn,
        *,
        output_type: Optional[str] = None,
        output_facets: Optional[Mapping[str, Mapping[str, str]]] = None,
        facet_observers: Optional[Mapping[str, FacetObserver]] = None,
        seed_random: bool = True,
        deterministic: bool = True,
        clock: Clock = perf_clock,
    ) -> None:
        """
        ``output_type``
            Artifact type name used when ``fn`` returns a bare payload.
        ``output_facets``
            *Declared* facets per artifact type, merged onto emitted artifacts
            but deliberately excluded from ``observed_facets``.
        ``facet_observers``
            Deterministic payload inspectors that read facets back off the
            data. These *do* enter ``observed_facets``.
        ``deterministic``
            What this adapter claims via :meth:`behavior_contract`. It is a
            claim, not a proof — the determinism probe is what settles it.
        """
        self.name = name
        self._fn = fn
        self._output_type = output_type
        self._output_facets = {k: dict(v) for k, v in (output_facets or {}).items()}
        self._facet_observers = dict(facet_observers or {})
        self._seed_random = seed_random
        self._deterministic = deterministic
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        return BehaviorContract(
            deterministic=self._deterministic,
            idempotent=self._deterministic,
            retry_safe=True,
            side_effects=[],
            shadow_safe=True,
        )

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        try:
            raw = await self._call(inv)
        except Exception as exc:  # noqa: BLE001 - the boundary must not leak
            return failure_result(
                _classify(exc),
                f"{type(exc).__name__}: {exc}",
                exit_code=1,
                cost=CostProfile(latency_s=self._clock() - started),
            )

        elapsed = self._clock() - started

        if isinstance(raw, InvocationResult):
            # Respect an explicit result but still run facet observation, so a
            # hand-written component cannot opt out of being observed.
            return self._observe(raw, inv, elapsed)

        try:
            outputs = self._coerce(raw)
        except ValueError as exc:
            return failure_result(
                FaultClass.CAPABILITY_MISMATCH,
                str(exc),
                exit_code=1,
                cost=CostProfile(latency_s=elapsed),
            )

        finalized = finalize_outputs(
            outputs,
            observers=self._facet_observers,
            declared_facets=self._output_facets,
            producer=inv.producer_id,
            derived_from=input_ids(inv),
        )
        return InvocationResult(
            ok=True,
            outputs=finalized.outputs,
            cost=CostProfile(latency_s=elapsed),
            logs="\n".join(finalized.notes),
            exit_code=0,
            observed_facets=finalized.observed_facets,
        )

    # -- internals ----------------------------------------------------------

    async def _call(self, inv: Invocation) -> Any:
        """Run the callable with the global RNG pinned to ``inv.seed``."""
        state = random.getstate() if self._seed_random else None
        if self._seed_random:
            random.seed(inv.seed)
        try:
            result = self._fn(inv)
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            if state is not None:
                random.setstate(state)

    def _coerce(self, raw: Any) -> dict[str, Artifact]:
        if isinstance(raw, Artifact):
            return {raw.type_name: raw}
        if isinstance(raw, Mapping) and all(isinstance(v, Artifact) for v in raw.values()):
            return {str(k): v for k, v in raw.items()}
        if self._output_type is None:
            raise ValueError(
                f"component '{self.name}' returned a bare payload of type "
                f"{type(raw).__name__} but no output_type was configured; the "
                "adapter refuses to guess which artifact type this is"
            )
        return {
            self._output_type: draft_artifact(type_name=self._output_type, payload=raw)
        }

    def _observe(
        self, result: InvocationResult, inv: Invocation, elapsed: float
    ) -> InvocationResult:
        finalized = finalize_outputs(
            result.outputs,
            observers=self._facet_observers,
            declared_facets=self._output_facets,
            producer=inv.producer_id,
            derived_from=input_ids(inv),
        )
        logs = "\n".join(part for part in [result.logs, *finalized.notes] if part)
        observed = {**finalized.observed_facets, **result.observed_facets}
        cost = result.cost if result.cost.latency_s else result.cost + CostProfile(latency_s=elapsed)
        return result.model_copy(
            update={
                "outputs": finalized.outputs,
                "observed_facets": observed,
                "logs": logs,
                "cost": cost,
            }
        )


def _classify(exc: Exception) -> FaultClass:
    """Map a Python exception onto a fault class.

    Coarse on purpose. The adapter knows only the shape of the failure; the
    diagnoser combines this with signals from the rest of the run before
    committing to a root cause. A missing import is an environment problem no
    matter which node raised it; a TypeError on the way in means the component
    was handed something it cannot accept.
    """
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return FaultClass.ENVIRONMENT
    if isinstance(exc, (MemoryError, OSError)):
        return FaultClass.ENVIRONMENT
    if isinstance(exc, TimeoutError):
        return FaultClass.TOOL_FAILURE
    if isinstance(exc, (TypeError, KeyError, AttributeError)):
        return FaultClass.ARTIFACT_CONTRACT
    if isinstance(exc, ValueError):
        return FaultClass.CONFIGURATION
    return FaultClass.TOOL_FAILURE


def function_error(exc: Exception) -> str:
    """Fault-shaped error line for an exception raised by a wrapped callable."""
    return error_line(_classify(exc), f"{type(exc).__name__}: {exc}")


__all__ = ["PythonFn", "PythonFnReturn", "PythonFunctionAdapter", "function_error"]
