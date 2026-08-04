"""The invocation boundary, and the adapters that cross it.

This is the public extension point: everything above it reasons about
components only through :class:`Invocation` and :class:`InvocationResult`, so
adding a component type means implementing :class:`ComponentAdapter` here and
nothing else changes.

The four adapters are exported by name because they are what a user reaches
for. The rest of the package's helpers stay in `base` — they are for writing
adapters, not for calling them.
"""

from agentcoop.components.base import (
    AdapterRegistry,
    ComponentAdapter,
    Invocation,
    InvocationResult,
    behavior_of,
    error_line,
    finalize_outputs,
    make_artifact,
)
from agentcoop.components.coding_agent import CodingAgentAdapter
from agentcoop.components.container import ContainerAdapter, docker_available
from agentcoop.components.python_fn import PythonFunctionAdapter
from agentcoop.components.subprocess_adapter import SubprocessAdapter

__all__ = [
    "Invocation",
    "InvocationResult",
    "ComponentAdapter",
    "AdapterRegistry",
    "behavior_of",
    "error_line",
    "finalize_outputs",
    "make_artifact",
    "PythonFunctionAdapter",
    "SubprocessAdapter",
    "ContainerAdapter",
    "CodingAgentAdapter",
    "docker_available",
]
