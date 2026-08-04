"""Task suites, fault injection, and the baseline comparison harness.

The package exists to make three claims falsifiable rather than asserted:
that composition helps where it is necessary, that the system declines it
where it is harmful, and that failures are blamed on the thing that actually
broke. Each has a corresponding construct here — the ``regime`` field, the
``multi_harmful`` tasks, and the ground-truth blame targets on injected
faults.
"""

from agentcoop.bench.baselines import (
    AgentCoOpSystem,
    BestSingleComponent,
    CodingAgentBaseline,
    EqualBudgetSingleAgent,
    SameWorkflowSingleAgent,
    SystemOutput,
    SystemUnderTest,
    default_systems,
)
from agentcoop.bench.behaviors import (
    SyntheticAdapter,
    behavior_names,
    build_adapter,
    get_behavior,
    register_behavior,
)
from agentcoop.bench.faults import (
    EXPECTED_FAULT_CLASS,
    SILENT_MECHANISMS,
    FaultyAdapter,
    Mechanism,
    expected_fault_class,
    install_faults,
    is_silent,
    serialize_subgoals,
)
from agentcoop.bench.harness import (
    Aggregate,
    BenchHarness,
    BenchReport,
    BlameScore,
    SystemReport,
    TaskScore,
    certify_environment,
    contract_summary,
    score,
)
from agentcoop.bench.task import (
    BenchEnvironment,
    BenchSuite,
    BenchTask,
    ComponentSpec,
    GroundTruth,
    InjectedFault,
    build_environment,
)

__all__ = [
    "BenchTask",
    "BenchSuite",
    "BenchEnvironment",
    "ComponentSpec",
    "GroundTruth",
    "InjectedFault",
    "build_environment",
    "Mechanism",
    "SILENT_MECHANISMS",
    "EXPECTED_FAULT_CLASS",
    "FaultyAdapter",
    "install_faults",
    "is_silent",
    "expected_fault_class",
    "serialize_subgoals",
    "SyntheticAdapter",
    "build_adapter",
    "register_behavior",
    "behavior_names",
    "get_behavior",
    "SystemOutput",
    "SystemUnderTest",
    "AgentCoOpSystem",
    "BestSingleComponent",
    "EqualBudgetSingleAgent",
    "SameWorkflowSingleAgent",
    "CodingAgentBaseline",
    "default_systems",
    "BenchHarness",
    "BenchReport",
    "SystemReport",
    "TaskScore",
    "BlameScore",
    "Aggregate",
    "score",
    "contract_summary",
    "certify_environment",
]
