from __future__ import annotations

from core.contracts import TaskSpec
from methods import METHODS
from methods.base import MethodResult
from runtime import Runtime


def run_method(method_name: str, task: TaskSpec, runtime: Runtime) -> MethodResult:
    if method_name not in METHODS:
        raise ValueError(f"Unknown method: {method_name}")
    return METHODS[method_name](task, runtime)
