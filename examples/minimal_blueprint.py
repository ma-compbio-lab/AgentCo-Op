from __future__ import annotations

from dynaforge import BlueprintExecutor
from dynaforge.ir import (
    BudgetSpec,
    EdgeSpec,
    GateSpec,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    TaskSpec,
    TriggerExpr,
    WorkflowBlueprint,
)


def build_blueprint() -> WorkflowBlueprint:
    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="Planner",
        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
        cacheable=True,
    )
    worker = NodeSpec(
        node_id="worker",
        kind=NodeKind.agent,
        role="Worker",
        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
        io={"output_schema": {"type": "object", "required": ["result"]}},
    )
    reviewer = NodeSpec(
        node_id="reviewer",
        kind=NodeKind.evaluator,
        role="Reviewer",
        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
    )

    return WorkflowBlueprint(
        task=TaskSpec(
            task_id="minimal-demo",
            title="Minimal Demo",
            description="A minimal blueprint showing base graph plus gated reviewer subgraph.",
        ),
        budget=BudgetSpec(max_total_usd=5.0, max_total_tokens=10_000),
        base_nodes=[planner, worker],
        base_edges=[EdgeSpec(edge_id="e1", src="planner", dst="worker")],
        subgraphs=[
            {
                "subgraph_id": "sg_review",
                "purpose": "Optional review path",
                "nodes": [reviewer],
                "edges": [],
                "entry_nodes": ["reviewer"],
                "exit_nodes": ["reviewer"],
                "default_enabled": False,
            }
        ],
        gates=[
            GateSpec(
                gate_id="low_conf_review",
                trigger=TriggerExpr(any_of=[{"field": "report.confidence", "op": "<", "value": 0.65}]),
                enable_subgraphs=["sg_review"],
            )
        ],
        meta={"demo": True},
    )


if __name__ == "__main__":
    executor = BlueprintExecutor()
    report = executor.execute_blueprint(build_blueprint())
    print(report.model_dump_json(indent=2))

