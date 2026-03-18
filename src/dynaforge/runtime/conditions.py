from __future__ import annotations

from typing import Any, Mapping, Sequence

from dynaforge.ir.schema import AtomicCondition, ConditionOp, TriggerExpr


def resolve_field(context: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = context
    for segment in dotted_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(segment)
        else:
            current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def evaluate_atomic(condition: AtomicCondition, context: Mapping[str, Any]) -> bool:
    actual = resolve_field(context, condition.field)
    expected = condition.value
    op = condition.op
    if op == ConditionOp.lt:
        return actual is not None and actual < expected
    if op == ConditionOp.le:
        return actual is not None and actual <= expected
    if op == ConditionOp.eq:
        return actual == expected
    if op == ConditionOp.ne:
        return actual != expected
    if op == ConditionOp.ge:
        return actual is not None and actual >= expected
    if op == ConditionOp.gt:
        return actual is not None and actual > expected
    if op == ConditionOp.contains:
        if actual is None:
            return False
        if isinstance(actual, Mapping):
            return expected in actual
        if isinstance(actual, (str, bytes, set, frozenset)):
            return expected in actual
        if isinstance(actual, Sequence):
            return expected in actual
        return False
    if op == ConditionOp.in_:
        return actual in expected if expected is not None else False
    raise ValueError(f"Unsupported condition op: {op}")


def evaluate_trigger(trigger: TriggerExpr, context: Mapping[str, Any]) -> bool:
    all_results = []
    for item in trigger.all_of:
        if isinstance(item, AtomicCondition):
            all_results.append(evaluate_atomic(item, context))
        else:
            all_results.append(evaluate_trigger(item, context))

    any_results = []
    for item in trigger.any_of:
        if isinstance(item, AtomicCondition):
            any_results.append(evaluate_atomic(item, context))
        else:
            any_results.append(evaluate_trigger(item, context))

    not_result = None
    if trigger.not_ is not None:
        if isinstance(trigger.not_, AtomicCondition):
            not_result = not evaluate_atomic(trigger.not_, context)
        else:
            not_result = not evaluate_trigger(trigger.not_, context)

    all_ok = all(all_results) if all_results else True
    any_ok = any(any_results) if any_results else True
    not_ok = not_result if not_result is not None else True
    return all_ok and any_ok and not_ok
