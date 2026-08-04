"""Benchmark suites.

A suite is pure data: tasks, components, ground truth, and the faults planted
in them. Nothing here runs. :mod:`agentcoop.bench.harness` is what executes a
suite, and only when something explicitly asks it to.
"""

from agentcoop.bench.suites.synthetic import synthetic_suite

__all__ = ["synthetic_suite"]
