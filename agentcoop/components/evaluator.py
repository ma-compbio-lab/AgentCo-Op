"""Run a named check as a workflow node.

`Verify(body, verifier)` lowers to a verifier node that runs through this
adapter. The adapter resolves ``verifier`` in the check registry, builds a
:class:`CheckContext`, runs the check, and emits the resulting
:class:`CheckResult` as a typed artifact so verification becomes part of the
lineage rather than a side effect.

The rule this module exists to enforce, stated once and applied everywhere
below: **an evaluator that could not run is not an evaluator that passed.**
Every path where the check cannot reach a verdict — registry missing, check
name unregistered, context unbuildable, implementation raised, implementation
returned something that is not a ``CheckResult`` — produces
``CheckStatus.UNAVAILABLE`` and ``ok=False``. None of them produce a pass.
Systems that get this wrong do not look broken; they look like they are
passing all their checks.

The check registry is imported *inside* the function body, not at module
scope. :mod:`agentcoop.evaluate` builds adapters of its own from this package,
so a top-level import would close a cycle. Injecting a registry through the
constructor skips the import entirely, which is what execution and probe runs
do.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping, Optional

from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus, SignalSource
from agentcoop.ir.faults import FaultClass

from agentcoop.components.base import (
    Clock,
    Invocation,
    InvocationResult,
    draft_artifact,
    error_line,
    finalize_outputs,
    input_ids,
    perf_clock,
)

#: Builds whatever ``CheckFn`` expects to receive. Injected so the adapter
#: never has to guess how the evaluation layer wants its context assembled.
CheckContextFactory = Callable[[Invocation], Any]

#: Module names searched for ``CheckContext`` / a default ``CheckRegistry``.
#: Tried in order; the first that yields a usable object wins.
_REGISTRY_MODULES = ("agentcoop.evaluate.registry", "agentcoop.evaluate")
_CONTEXT_MODULES = (
    "agentcoop.evaluate.contract",
    "agentcoop.evaluate.context",
    "agentcoop.evaluate.registry",
    "agentcoop.evaluate",
)
_REGISTRY_ATTRS = ("default_registry", "build_default_registry", "DEFAULT_REGISTRY", "REGISTRY")


def load_check_registry() -> tuple[Optional[Any], str]:
    """Lazily obtain the default check registry.

    Returns ``(registry, error)``. Never raises: a missing or broken evaluation
    layer must degrade to UNAVAILABLE checks, not to an exception that some
    caller upstream turns into a generic failure of the component under test.
    """
    tried: list[str] = []
    for module_name in _REGISTRY_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            tried.append(f"{module_name}: {type(exc).__name__}: {exc}")
            continue
        for attr in _REGISTRY_ATTRS:
            candidate = getattr(module, attr, None)
            if candidate is None:
                continue
            try:
                resolved = candidate() if callable(candidate) else candidate
            except Exception as exc:  # noqa: BLE001
                tried.append(f"{module_name}.{attr}: {type(exc).__name__}: {exc}")
                continue
            if hasattr(resolved, "get"):
                return resolved, ""
        tried.append(f"{module_name}: no default registry attribute")
    return None, "; ".join(tried) or "no evaluation modules importable"


def load_check_context_class() -> Optional[type]:
    """Locate ``CheckContext`` without importing the evaluation layer eagerly."""
    for module_name in _CONTEXT_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001
            continue
        candidate = getattr(module, "CheckContext", None)
        if isinstance(candidate, type):
            return candidate
    return None


class EvaluatorAdapter:
    """Run one registered check and emit its :class:`CheckResult`."""

    def __init__(
        self,
        name: str = "evaluator",
        *,
        check_registry: Optional[Any] = None,
        context_factory: Optional[CheckContextFactory] = None,
        dossier: Optional[Any] = None,
        workflow: Optional[Any] = None,
        trace: Optional[Any] = None,
        output_type: str = "CheckResult",
        clock: Clock = perf_clock,
    ) -> None:
        self.name = name
        self.output_type = output_type
        self._registry = check_registry
        self._context_factory = context_factory
        self._dossier = dossier
        self._workflow = workflow
        self._trace = trace
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        return BehaviorContract(
            deterministic=True, idempotent=True, retry_safe=True, shadow_safe=True
        )

    # -- resolution ---------------------------------------------------------

    def resolve_registry(self) -> tuple[Optional[Any], str]:
        if self._registry is not None:
            return self._registry, ""
        return load_check_registry()

    def build_context(self, inv: Invocation) -> tuple[Optional[Any], str]:
        if self._context_factory is not None:
            try:
                return self._context_factory(inv), ""
            except Exception as exc:  # noqa: BLE001
                return None, f"context factory raised {type(exc).__name__}: {exc}"

        context_cls = load_check_context_class()
        if context_cls is None:
            return None, "CheckContext is not importable from agentcoop.evaluate"
        if self._dossier is None:
            return None, (
                "no dossier available to build a CheckContext; pass dossier= or "
                "context_factory= when constructing the EvaluatorAdapter"
            )
        # Keyed by artifact id, matching CheckContext's documented contract.
        # Adding a second entry per artifact under its type name would double
        # every artifact for any check that iterates ``artifacts.values()``.
        artifacts = {art.artifact_id: art for art in inv.inputs.values()}
        try:
            return (
                context_cls(
                    dossier=self._dossier,
                    workflow=self._workflow,
                    artifacts=artifacts,
                    trace=self._trace,
                    cost=CostProfile(),
                    params=dict(inv.config.get("params", {}) or {}),
                    subject=inv.config.get("subject"),
                ),
                "",
            )
        except Exception as exc:  # noqa: BLE001
            return None, f"could not build CheckContext: {type(exc).__name__}: {exc}"

    # -- invocation ---------------------------------------------------------

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        impl = str(inv.config.get("check") or inv.config.get("verifier") or inv.config.get("impl") or "")
        check_id = str(inv.config.get("check_id") or impl or "unnamed_check")
        level = _as_level(inv.config.get("level"))
        blocking = bool(inv.config.get("blocking", False))
        subject = inv.config.get("subject")
        subject = str(subject) if subject is not None else None

        def unavailable(summary: str, **evidence: Any) -> InvocationResult:
            return self._emit(
                inv,
                CheckResult(
                    check_id=check_id,
                    level=level,
                    status=CheckStatus.UNAVAILABLE,
                    source=SignalSource.DETERMINISTIC,
                    summary=summary,
                    subject=subject,
                    subject_kind=_as_subject_kind(inv.config.get("subject_kind")),
                    evidence=dict(evidence),
                    blocking=blocking,
                ),
                started,
                evaluator_error=summary,
            )

        if not impl:
            return unavailable(
                "no check implementation named: config must set 'check' (or 'verifier')"
            )

        registry, registry_error = self.resolve_registry()
        if registry is None:
            return unavailable(
                f"check registry unavailable, so check '{impl}' did not run "
                "(unavailable is not a pass)",
                registry_error=registry_error,
            )

        try:
            fn = registry.get(impl)
        except Exception as exc:  # noqa: BLE001
            return unavailable(
                f"check '{impl}' is not registered: {type(exc).__name__}: {exc}"
            )
        if fn is None or not callable(fn):
            return unavailable(f"check '{impl}' resolved to something that is not callable")

        context, context_error = self.build_context(inv)
        if context is None:
            return unavailable(
                f"check '{impl}' could not be given a context", context_error=context_error
            )

        try:
            result = fn(context)
        except Exception as exc:  # noqa: BLE001
            # An evaluator that crashes tells us nothing about the subject. It
            # must not be reported as a failure *of the subject*.
            return unavailable(
                f"check '{impl}' raised {type(exc).__name__}: {exc}",
                evaluator_exception=type(exc).__name__,
            )

        if not isinstance(result, CheckResult):
            return unavailable(
                f"check '{impl}' returned {type(result).__name__}, not a CheckResult"
            )

        return self._emit(inv, result, started)

    def _emit(
        self,
        inv: Invocation,
        result: CheckResult,
        started: float,
        *,
        evaluator_error: str = "",
    ) -> InvocationResult:
        artifact = draft_artifact(
            type_name=self.output_type,
            payload=result.model_dump(mode="json"),
            notes=[f"check_status={result.status.value}"],
        )
        finalized = finalize_outputs(
            {self.output_type: artifact},
            producer=inv.producer_id,
            derived_from=input_ids(inv),
        )
        observed = {
            self.output_type: {
                "check_status": result.status.value,
                "check_level": result.level.value,
                "signal_source": result.source.value,
            }
        }

        errors: list[str] = []
        if evaluator_error:
            errors.append(error_line(FaultClass.EVALUATOR_FAILURE, evaluator_error))
        elif result.status == CheckStatus.FAIL:
            # Untagged: a failing check is a signal about the subject, and the
            # adapter has no basis to name a root cause. Diagnosis does that.
            errors.append(f"check '{result.check_id}' failed: {result.summary}")
        elif result.status == CheckStatus.INCONCLUSIVE:
            errors.append(
                error_line(
                    FaultClass.EVALUATOR_FAILURE,
                    f"check '{result.check_id}' is inconclusive: {result.summary}",
                )
            )

        return InvocationResult(
            ok=result.status in (CheckStatus.PASS, CheckStatus.WARN),
            outputs=finalized.outputs,
            cost=CostProfile(latency_s=self._clock() - started),
            logs="\n".join([f"{result.check_id}: {result.status.value}", *finalized.notes]),
            errors=errors,
            exit_code=0 if result.status in (CheckStatus.PASS, CheckStatus.WARN) else 1,
            observed_facets=observed,
        )


def _as_level(value: Any) -> CheckLevel:
    if isinstance(value, CheckLevel):
        return value
    if isinstance(value, str):
        try:
            return CheckLevel(value)
        except ValueError:
            return CheckLevel.HARD
    return CheckLevel.HARD


def _as_subject_kind(value: Any) -> Any:
    allowed = {"node", "edge", "artifact", "workflow", "claim", "component"}
    return value if isinstance(value, str) and value in allowed else "node"


def check_result_from_artifact(payload: Any) -> Optional[CheckResult]:
    """Parse a :class:`CheckResult` back out of an emitted artifact payload."""
    if not isinstance(payload, Mapping):
        return None
    try:
        return CheckResult.model_validate(dict(payload))
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "CheckContextFactory",
    "load_check_registry",
    "load_check_context_class",
    "EvaluatorAdapter",
    "check_result_from_artifact",
]
