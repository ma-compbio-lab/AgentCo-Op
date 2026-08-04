"""The check registry: names to implementations.

A plain object rather than a module-level global, for the same reason
:class:`~agentcoop.ir.artifacts.TypeRegistry` is: tests, probe runs, and
shadow validations build isolated registries so that a check registered
during one run cannot leak into the evaluation of another and quietly change
a verdict.

Registration is explicit and collision-safe. Silently overwriting a check
implementation would let a repair "fix" a failure by redefining the check
that caught it, which is precisely the class of self-serving repair this
rebuild exists to rule out.
"""

from __future__ import annotations

from typing import Optional

from agentcoop.evaluate.checks import artifact as artifact_checks
from agentcoop.evaluate.checks import claim_checks
from agentcoop.evaluate.checks import hard as hard_checks
from agentcoop.evaluate.checks import preference as preference_checks
from agentcoop.evaluate.checks import process as process_checks
from agentcoop.evaluate.checks import resource as resource_checks
from agentcoop.evaluate.contract import CheckFn
from agentcoop.ir.checks import CheckLevel


class CheckRegistry:
    """Name -> check implementation."""

    def __init__(self) -> None:
        self._fns: dict[str, CheckFn] = {}
        self._levels: dict[str, CheckLevel] = {}

    def register(
        self,
        name: str,
        fn: CheckFn,
        *,
        level: Optional[CheckLevel] = None,
        overwrite: bool = False,
    ) -> None:
        """Register ``fn`` under ``name``.

        ``level`` is advisory metadata for introspection and CLI listing; the
        authoritative level of a result is the one on the :class:`CheckSpec`
        that invoked it.
        """
        if not name:
            raise ValueError("check name must be non-empty")
        if name in self._fns and not overwrite:
            raise KeyError(
                f"check '{name}' is already registered; pass overwrite=True to replace it"
            )
        self._fns[name] = fn
        if level is not None:
            self._levels[name] = level
        elif name in self._levels and overwrite:
            self._levels.pop(name, None)

    def get(self, name: str) -> CheckFn:
        fn = self._fns.get(name)
        if fn is None:
            raise KeyError(f"check '{name}' is not registered")
        return fn

    def has(self, name: str) -> bool:
        return name in self._fns

    def names(self) -> list[str]:
        return sorted(self._fns)

    def level_of(self, name: str) -> Optional[CheckLevel]:
        return self._levels.get(name)

    def names_at(self, level: CheckLevel) -> list[str]:
        return sorted(n for n, lv in self._levels.items() if lv == level)


#: Built-in implementations, excluding ``invariant`` which needs the registry
#: itself in order to dispatch on ``Invariant.check``.
_BUILTINS: dict[CheckLevel, dict[str, CheckFn]] = {
    CheckLevel.HARD: {
        "exit_status": hard_checks.exit_status,
        "schema_valid": hard_checks.schema_valid,
        "required_outputs_present": hard_checks.required_outputs_present,
        "no_empty_result": hard_checks.no_empty_result,
        "min_items": hard_checks.min_items,
    },
    CheckLevel.ARTIFACT: {
        "facets_declared": artifact_checks.facets_declared,
        "facet_match": artifact_checks.facet_match,
        "coverage_threshold": artifact_checks.coverage_threshold,
        "provenance_complete": artifact_checks.provenance_complete,
        "no_silent_empty": artifact_checks.no_silent_empty,
    },
    CheckLevel.PROCESS: {
        "required_steps_ran": process_checks.required_steps_ran,
        "verifier_present": process_checks.verifier_present,
        "sensitivity_ran": process_checks.sensitivity_ran,
        "negative_control_ran": process_checks.negative_control_ran,
    },
    CheckLevel.CLAIM: {
        "claim_bundle_wellformed": claim_checks.claim_bundle_wellformed,
        "claim_traceable": claim_checks.claim_traceable,
        "claim_has_alternatives": claim_checks.claim_has_alternatives,
        "claim_uncertainty_declared": claim_checks.claim_uncertainty_declared,
    },
    CheckLevel.PREFERENCE: {
        "expert_pairwise": preference_checks.expert_pairwise,
    },
    CheckLevel.RESOURCE: {
        "within_budget": resource_checks.within_budget,
        "within_wall_time": resource_checks.within_wall_time,
        "reproducible": resource_checks.reproducible,
    },
}


def register_builtins(registry: CheckRegistry, *, overwrite: bool = False) -> CheckRegistry:
    """Populate ``registry`` with every built-in check.

    ``invariant`` is registered last and closed over ``registry`` so that a
    dossier invariant naming any *other* registered check — including one a
    caller registered afterwards, since the closure resolves at call time —
    dispatches to it. That is what keeps the taxonomy of hard constraints
    open without a hard-coded table in this package.
    """
    for level, table in _BUILTINS.items():
        for name in sorted(table):
            registry.register(name, table[name], level=level, overwrite=overwrite)
    registry.register(
        "invariant",
        hard_checks.make_invariant_check(registry),
        level=CheckLevel.HARD,
        overwrite=overwrite,
    )
    return registry


def default_registry() -> CheckRegistry:
    """A fresh registry with the built-in checks installed."""
    return register_builtins(CheckRegistry())


def builtin_names() -> list[str]:
    return sorted({name for table in _BUILTINS.values() for name in table} | {"invariant"})


__all__ = [
    "CheckRegistry",
    "register_builtins",
    "default_registry",
    "builtin_names",
]
