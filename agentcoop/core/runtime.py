"""Runtime orchestrator.

Executes a `WorkflowBlueprint` as an async DAG walk. After every node the
gate evaluator fires, the patch planner runs, and patches are applied
in-place — all bounded by `GateLimits`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from agentcoop.backends import BackendRegistry, default_registry
from agentcoop.backends.base import NodeContext
from agentcoop.core.cost import CostLedger
from agentcoop.core.gates import (
    GateLimits,
    apply_patch,
    evaluate_gates,
    plan_patch,
)
from agentcoop.core.schema import (
    EdgeSpec,
    NodeResult,
    NodeSpec,
    RunState,
    WorkflowBlueprint,
)
from agentcoop.core.tracing import TraceWriter, new_run_id
from agentcoop.memory.artifact_store import ArtifactStore
from agentcoop.memory.blackboard import Blackboard


@dataclass
class RuntimeConfig:
    backends: BackendRegistry | None = None
    limits: GateLimits = None  # type: ignore[assignment]
    run_root: str = "runs"
    raise_on_node_error: bool = False

    def __post_init__(self) -> None:
        if self.backends is None:
            self.backends = default_registry()
        if self.limits is None:
            self.limits = GateLimits()


@dataclass
class RunOutcome:
    state: RunState
    final: NodeResult | None
    trace_path: Path
    artifact_dir: Path


async def run_blueprint(
    blueprint: WorkflowBlueprint,
    *,
    config: RuntimeConfig | None = None,
    payload: dict[str, Any] | None = None,
) -> RunOutcome:
    cfg = config or RuntimeConfig()
    run_id = new_run_id()
    run_dir = Path(cfg.run_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceWriter(run_dir, run_id=run_id)
    artifacts = ArtifactStore(run_dir / "artifacts")
    blackboard = Blackboard()
    ledger = CostLedger()

    state = RunState(run_id=run_id, blueprint_id=blueprint.blueprint_id)
    trace.run_start(blueprint.blueprint_id, blueprint.task_profile.task_id)

    # Initial payload goes into shared memory under a well-known key.
    if payload:
        blackboard.write("orchestrator", "task_input", payload, visibility="shared_read")

    start_wall = time.monotonic()
    budget = blueprint.budget
    last_result: NodeResult | None = None

    # Iterate until terminated, budget exceeded, or no nodes left to run.
    # We rebuild the DAG each outer loop because patches can add nodes/edges.
    executed: set[str] = set()
    while not state.terminated:
        graph = _build_dag(blueprint)
        ready = _ready_nodes(graph, executed, blueprint, state)
        if not ready:
            break
        # Execute ready nodes sequentially for now (parallelism is a later optimization).
        for node_id in ready:
            if state.terminated:
                break
            node = _find_node(blueprint, node_id)
            if node is None:
                continue
            if _over_budget(state, budget, start_wall):
                state.terminated = True
                state.termination_reason = "budget_exceeded"
                trace.terminate("budget_exceeded")
                break

            node_payload = _build_node_payload(node, blackboard, payload)
            ctx = NodeContext(
                run_id=run_id,
                task_id=blueprint.task_profile.task_id,
                memory_summary=blackboard.summarize_for(node.node_id),
                shared_inputs={"payload": node_payload},
            )
            backend = cfg.backends.get(node.backend)
            trace.node_start(node.node_id, input_hash=_hash(node_payload))

            try:
                result = await backend.execute(node, node_payload, ctx)
            except Exception as exc:  # surface as failed node, do not crash runtime
                result = NodeResult(
                    node_id=node.node_id,
                    ok=False,
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
                if cfg.raise_on_node_error:
                    trace.node_end(
                        node.node_id,
                        ok=False,
                        errors=result.errors,
                    )
                    raise

            trace.node_end(
                node.node_id,
                ok=result.ok,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                latency_s=result.latency_s,
                confidence=result.confidence,
                errors=result.errors,
            )
            state.tokens_used += result.tokens_in + result.tokens_out
            state.cost_used_usd += result.cost_usd
            state.node_results[node.node_id] = result
            ledger.record(node.node_id, "mock-llm", result.tokens_in, result.tokens_out)

            blackboard.write(
                node.node_id,
                f"output:{node.node_id}",
                result.output,
                visibility="shared_read",
            )
            for i, art in enumerate(result.artifacts):
                # Callers that write to the run's artifact dir keep paths as-is.
                pass

            last_result = result

            # Gate evaluation → patch
            fired = evaluate_gates(blueprint, state, node, result)
            for policy, reason in fired:
                state.gate_activations[policy.name] = state.gate_activations.get(policy.name, 0) + 1
                trace.gate_triggered(policy.name, policy.action, reason)
                patch = plan_patch(policy, node, reason)
                if apply_patch(blueprint, state, patch, cfg.limits):
                    trace.patch_applied(patch.op.value, patch.target_node, patch.reason)
                else:
                    trace.write("patch_skipped", op=patch.op.value, reason=reason)

            if not result.ok and cfg.limits.max_same_node_repairs == 0:
                state.terminated = True
                state.termination_reason = "node_failed_no_retry"
                trace.terminate(state.termination_reason)
                break

            # If a retry was scheduled for this node, don't mark it executed.
            retries_now = state.node_retries.get(node.node_id, 0)
            if retries_now == 0 or node.node_id in executed:
                executed.add(node.node_id)
            else:
                # Pop from `executed` to allow re-execution next pass.
                executed.discard(node.node_id)

        state.elapsed_s = time.monotonic() - start_wall
        if len(executed) >= len(blueprint.nodes) and not state.terminated:
            break
        if state.patches_applied >= cfg.limits.max_graph_patches:
            # Continue with what we have — but stop adding more patches.
            pass

    trace.run_end(
        status="ok" if not state.terminated else (state.termination_reason or "terminated"),
        tokens=state.tokens_used,
        cost_usd=state.cost_used_usd,
        elapsed_s=state.elapsed_s,
        patches=state.patches_applied,
    )

    # Persist blueprint + final state for inspection.
    (run_dir / "blueprint.json").write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")

    return RunOutcome(
        state=state,
        final=_pick_final_result(blueprint, state, last_result),
        trace_path=trace.path,
        artifact_dir=Path(artifacts.root),
    )


def _pick_final_result(
    blueprint: WorkflowBlueprint,
    state: RunState,
    fallback: NodeResult | None,
) -> NodeResult | None:
    """Prefer the sink node's result (no forward outgoing edges) over the last-run node."""
    graph = _build_dag(blueprint)
    back = _back_edges(graph)
    sinks = [
        nid
        for nid in graph.nodes
        if not [
            (u, v) for u, v in graph.out_edges(nid) if (u, v) not in back
        ]
    ]
    # Prefer finalizer/formatter roles among sinks.
    role_priority = {"formatter": 0, "integrator": 1, "reviewer": 2}
    sinks_with_result = [
        (role_priority.get(_role_of(blueprint, nid), 99), nid)
        for nid in sinks
        if nid in state.node_results
    ]
    if sinks_with_result:
        sinks_with_result.sort()
        return state.node_results[sinks_with_result[0][1]]
    return fallback


def _role_of(blueprint: WorkflowBlueprint, node_id: str) -> str:
    for n in blueprint.nodes:
        if n.node_id == node_id:
            return n.role
    return ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_dag(blueprint: WorkflowBlueprint) -> nx.DiGraph:
    g = nx.DiGraph()
    for n in blueprint.nodes:
        g.add_node(n.node_id)
    for e in blueprint.edges:
        if e.source and e.target:
            g.add_edge(e.source, e.target, condition=e.condition, payload_map=e.payload_map)
    return g


def _ready_nodes(
    graph: nx.DiGraph,
    executed: set[str],
    blueprint: WorkflowBlueprint,
    state: RunState,
) -> list[str]:
    """A node is ready when its forward dependencies have executed.

    Forward edges (unconditional or conditional) impose ordering; back-edges
    on cycles are detected and treated as optional so the graph remains
    progressable.
    """
    back_edges = _back_edges(graph)
    ready: list[str] = []
    for node_id in graph.nodes:
        if node_id in executed:
            if state.node_retries.get(node_id, 0) > 0:
                state.node_retries[node_id] -= 1
                ready.append(node_id)
            continue
        preds = [
            u for u, v in graph.in_edges(node_id) if (u, v) not in back_edges
        ]
        if all(p in executed for p in preds):
            ready.append(node_id)
    ready.sort()
    return ready


def _back_edges(graph: nx.DiGraph) -> set[tuple[str, str]]:
    """Return edges that would create a cycle if traversed forward-only."""
    back: set[tuple[str, str]] = set()
    working = graph.copy()
    while True:
        try:
            cycle = nx.find_cycle(working, orientation="original")
        except nx.NetworkXNoCycle:
            break
        u, v = cycle[-1][0], cycle[-1][1]
        back.add((u, v))
        working.remove_edge(u, v)
    return back


def _find_node(blueprint: WorkflowBlueprint, node_id: str) -> NodeSpec | None:
    for n in blueprint.nodes:
        if n.node_id == node_id:
            return n
    return None


def _build_node_payload(
    node: NodeSpec,
    blackboard: Blackboard,
    seed_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if seed_payload:
        payload["task_input"] = seed_payload
    for key in blackboard.keys_for(node.node_id):
        if key == "task_input" or key.startswith("output:"):
            payload[key] = blackboard.get(node.node_id, key)
    return payload


def _hash(obj: Any) -> str:
    try:
        blob = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    except TypeError:
        blob = str(obj).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:10]


def _over_budget(state: RunState, budget, start_wall: float) -> bool:
    if budget.max_tokens and state.tokens_used >= budget.max_tokens:
        return True
    if budget.max_cost_usd and state.cost_used_usd >= budget.max_cost_usd:
        return True
    if budget.max_wall_time_s and (time.monotonic() - start_wall) >= budget.max_wall_time_s:
        return True
    return False


__all__ = ["run_blueprint", "RuntimeConfig", "RunOutcome"]
