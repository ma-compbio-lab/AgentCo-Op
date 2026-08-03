"""Benchmark loaders + grader dispatch."""

from agentcoop.benchmarks.common import BenchmarkTask

from agentcoop.benchmarks import drop as drop  # noqa: F401
from agentcoop.benchmarks import gsm8k as gsm8k  # noqa: F401
from agentcoop.benchmarks import hotpotqa as hotpotqa  # noqa: F401
from agentcoop.benchmarks import humaneval as humaneval  # noqa: F401
from agentcoop.benchmarks import math as math_bench  # noqa: F401
from agentcoop.benchmarks import mbpp as mbpp  # noqa: F401


_LOADERS = {
    "gsm8k": gsm8k.load,
    "math": math_bench.load,
    "humaneval": humaneval.load,
    "mbpp": mbpp.load,
    "hotpotqa": hotpotqa.load,
    "drop": drop.load,
}


def load(dataset: str, split: str = "test", limit: int | None = None, aflow: bool = True) -> list[BenchmarkTask]:
    if dataset not in _LOADERS:
        raise KeyError(f"unknown dataset '{dataset}'; available: {sorted(_LOADERS)}")
    return _LOADERS[dataset](split=split, limit=limit, aflow=aflow)


def available_datasets() -> list[str]:
    return sorted(_LOADERS)


__all__ = ["BenchmarkTask", "load", "available_datasets"]
