from __future__ import annotations

import json
import os
import subprocess
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence, Set

from dynaforge.ir.schema import FailureType, NodeKind, NodeSpec, TraceAssertion, WorkflowBlueprint
from dynaforge.runtime.blame import BlameAssigner, FailureClassifier
from dynaforge.runtime.cache import ArtifactStore
from dynaforge.runtime.conditions import evaluate_trigger
from dynaforge.runtime.patching import DeterministicPatchPolicy, apply_patch_plan, compute_impacted_nodes
from dynaforge.runtime.reports import (
    BlameCandidate,
    BudgetLedger,
    ContractViolation,
    ExecutionCost,
    ExecutionReport,
    HardCheckResult,
    NodeExecutionResult,
    NodeTrace,
    SoftJudgeResult,
)

JsonDict = Dict[str, Any]


class NodeHandler(Protocol):
    def __call__(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
    ) -> NodeExecutionResult:
        ...


@dataclass
class NodeExecutionContext:
    blueprint: WorkflowBlueprint
    artifact_store: ArtifactStore
    active_subgraphs: Set[str]
    node_results: Dict[str, NodeExecutionResult]
    budget_remaining: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


class BlueprintExecutor:
    """Executes a typed workflow blueprint and can apply local repair loops."""

    def __init__(
        self,
        artifact_root: str | Path = ".dynaforge_artifacts",
        *,
        patch_policy: Optional[DeterministicPatchPolicy] = None,
        blame_assigner: Optional[BlameAssigner] = None,
    ):
        self.artifact_store = ArtifactStore(artifact_root)
        self.patch_policy = patch_policy or DeterministicPatchPolicy()
        self.blame_assigner = blame_assigner or BlameAssigner()
        self.failure_classifier = FailureClassifier()
        self._memoized_results: Dict[str, NodeExecutionResult] = {}

    def execute_blueprint(
        self,
        blueprint: WorkflowBlueprint,
        *,
        handlers: Optional[Mapping[str, NodeHandler]] = None,
        resume_state: Optional[Mapping[str, NodeExecutionResult]] = None,
        force_nodes: Optional[Set[str]] = None,
        workflow_id: Optional[str] = None,
    ) -> ExecutionReport:
        handlers = handlers or {}
        force_nodes = force_nodes or set()
        workflow_id = workflow_id or f"{blueprint.task.task_id}:{int(time.time())}"
        ledger = BudgetLedger(blueprint.budget)
        active_subgraphs = set(blueprint.default_active_subgraphs())
        active_subgraphs.update(self._subgraphs_containing_nodes(blueprint, force_nodes))
        disabled_edges: Set[str] = set()
        activated_gates: list[str] = []
        gate_activation_counts: Dict[str, int] = defaultdict(int)
        node_results: Dict[str, NodeExecutionResult] = {}
        traces: list[NodeTrace] = []
        contract_violations: list[ContractViolation] = []
        hard_checks: list[HardCheckResult] = []
        soft_judges: list[SoftJudgeResult] = []

        node_map = blueprint.node_map()
        executed: Set[str] = set()

        for node_id, result in (resume_state or {}).items():
            if node_id not in node_map or node_id in force_nodes:
                continue
            if result.failure_type != FailureType.none:
                continue
            executed.add(node_id)
            node_results[node_id] = result
            traces.append(
                NodeTrace(
                    node_id=node_id,
                    role=node_map[node_id].role,
                    status="cached",
                    outputs=result.outputs,
                    artifacts=result.artifacts,
                    trace=result.trace,
                    cost=ExecutionCost(),
                    confidence=result.confidence,
                )
            )

        failure_type = FailureType.none
        summary = "Workflow completed successfully."

        while True:
            active_nodes, active_edges = self._compose_active_graph(blueprint, active_subgraphs, disabled_edges)
            parent_map = self._parent_map(active_nodes, active_edges, node_results)
            ready = sorted(
                node_id
                for node_id in active_nodes
                if node_id not in executed and parent_map[node_id].issubset(executed)
            )

            if not ready:
                remaining = active_nodes - executed
                if remaining:
                    failure_type = FailureType.reasoning_inconsistency
                    summary = f"Execution deadlocked waiting on nodes: {sorted(remaining)}"
                break

            for node_id in ready:
                if ledger.exceeds_budget():
                    failure_type = FailureType.budget_exceeded
                    summary = "Budget exhausted before all nodes could execute."
                    break

                node = node_map[node_id]
                inputs = self._assemble_inputs(node_id, parent_map, active_edges, node_results)
                input_violations = self._validate_input_contract(node, inputs)
                if input_violations:
                    contract_violations.extend(input_violations)
                    result = NodeExecutionResult(
                        failure_type=FailureType.output_contract_violation,
                        error=f"Input contract validation failed at node {node_id}.",
                    )
                else:
                    result = self._run_node(node, inputs, blueprint, handlers, node_results, active_subgraphs)
                ledger.consume(result.cost)
                node_results[node_id] = result
                executed.add(node_id)

                trace_status = "cached" if result.cached else ("failed" if result.failure_type != FailureType.none else "success")
                traces.append(
                    NodeTrace(
                        node_id=node.node_id,
                        role=node.role,
                        status=trace_status,
                        inputs=inputs,
                        outputs=result.outputs,
                        artifacts=result.artifacts,
                        trace=result.trace,
                        cost=result.cost,
                        confidence=result.confidence,
                        failure_type=result.failure_type,
                        error=result.error,
                    )
                )

                output_violations = []
                if result.failure_type == FailureType.none:
                    output_violations = self._validate_output_contract(node, inputs, result.outputs)
                    contract_violations.extend(output_violations)
                if input_violations and failure_type == FailureType.none:
                    failure_type = FailureType.output_contract_violation
                    summary = f"Input contract validation failed at node {node_id}."
                elif output_violations and failure_type == FailureType.none:
                    failure_type = FailureType.output_contract_violation
                    summary = f"Output contract validation failed at node {node_id}."

                if result.failure_type != FailureType.none:
                    failure_type = result.failure_type
                    summary = result.error or f"Node {node_id} failed."
                    break

                self._maybe_activate_gates(
                    blueprint=blueprint,
                    report_context={
                        "report": {
                            "confidence": result.confidence,
                            "failure_type": failure_type.value,
                        },
                        "budget": ledger.snapshot().model_dump(),
                        "node": traces[-1].model_dump(),
                    },
                    active_subgraphs=active_subgraphs,
                    disabled_edges=disabled_edges,
                    activated_gates=activated_gates,
                    gate_activation_counts=gate_activation_counts,
                )

            if failure_type != FailureType.none:
                break

        if failure_type == FailureType.none and executed:
            hard_checks = self._run_hard_checks(blueprint)
            failing_checks = [check for check in hard_checks if not check.passed]
            if failing_checks:
                failure_type = self.failure_classifier.classify_hard_check(failing_checks[0])
                summary = f"Hard check {failing_checks[0].check_id} failed."

        if failure_type == FailureType.none and contract_violations:
            failure_type = FailureType.output_contract_violation

        if failure_type == FailureType.none and executed:
            trace_failures = self._run_trace_assertions(blueprint.eval.trace_assertions, node_results)
            contract_violations.extend(trace_failures)
            if trace_failures:
                failure_type = FailureType.output_contract_violation
                summary = "Trace assertions failed."

        if executed:
            soft_judges = self._run_soft_judges(blueprint, traces)

        report = ExecutionReport(
            workflow_id=workflow_id,
            task_id=blueprint.task.task_id,
            success=failure_type == FailureType.none,
            failure_type=failure_type,
            traces=traces,
            hard_checks=hard_checks,
            soft_judges=soft_judges,
            contract_violations=contract_violations,
            activated_gates=activated_gates,
            active_subgraphs=sorted(active_subgraphs),
            cost=ledger.consumed,
            confidence=self._aggregate_confidence(traces),
            summary=summary,
            node_results=node_results,
            meta={"executed_nodes": sorted(executed)},
        )
        report.blame_candidates = self.blame_assigner.assign(blueprint, report)
        return report

    def execute_with_repair(
        self,
        blueprint: WorkflowBlueprint,
        *,
        handlers: Optional[Mapping[str, NodeHandler]] = None,
        max_iterations: int = 3,
    ) -> tuple[WorkflowBlueprint, ExecutionReport]:
        current_blueprint = blueprint
        report = self.execute_blueprint(current_blueprint, handlers=handlers)
        for _ in range(max_iterations):
            if report.success:
                return current_blueprint, report
            patch_plan = self.patch_policy.propose(current_blueprint, report)
            impacted = compute_impacted_nodes(current_blueprint, patch_plan)
            current_blueprint = apply_patch_plan(current_blueprint, patch_plan)
            report = self.execute_blueprint(
                current_blueprint,
                handlers=handlers,
                resume_state=report.node_results,
                force_nodes=impacted,
            )
        return current_blueprint, report

    def _compose_active_graph(
        self,
        blueprint: WorkflowBlueprint,
        active_subgraphs: Set[str],
        disabled_edges: Set[str],
    ) -> tuple[Set[str], list[Any]]:
        nodes = {node.node_id for node in blueprint.base_nodes}
        edges = [edge for edge in blueprint.base_edges if edge.edge_id not in disabled_edges]
        subgraph_map = blueprint.subgraph_map()
        for subgraph_id in active_subgraphs:
            subgraph = subgraph_map.get(subgraph_id)
            if subgraph is None:
                continue
            nodes.update(node.node_id for node in subgraph.nodes)
            edges.extend(edge for edge in subgraph.edges if edge.edge_id not in disabled_edges)
        return nodes, edges

    @staticmethod
    def _parent_map(
        active_nodes: Set[str],
        active_edges: Sequence[Any],
        node_results: Mapping[str, NodeExecutionResult],
    ) -> Dict[str, Set[str]]:
        parents: Dict[str, Set[str]] = {node_id: set() for node_id in active_nodes}
        for edge in active_edges:
            condition_state = BlueprintExecutor._edge_condition_state(edge, node_results)
            if condition_state is False:
                continue
            parents[edge.dst].add(edge.src)
        return parents

    def _assemble_inputs(
        self,
        node_id: str,
        parent_map: Mapping[str, Set[str]],
        active_edges: Sequence[Any],
        node_results: Mapping[str, NodeExecutionResult],
    ) -> JsonDict:
        payload: JsonDict = {}
        inbound_edges = [edge for edge in active_edges if edge.dst == node_id]
        active_inbound_edges = [
            edge for edge in inbound_edges if self._edge_condition_state(edge, node_results) is not False
        ]
        for edge in active_inbound_edges:
            upstream = node_results.get(edge.src)
            if upstream is None:
                continue
            if not edge.mapping:
                payload[edge.src] = upstream.outputs
                continue
            for target_key, source_path in edge.mapping.items():
                payload[target_key] = self._extract_path(upstream.outputs, source_path)
        if not active_inbound_edges:
            for parent in parent_map.get(node_id, set()):
                upstream = node_results.get(parent)
                if upstream is not None:
                    payload[parent] = upstream.outputs
        return payload

    @staticmethod
    def _extract_path(source: JsonDict, dotted_path: Any) -> Any:
        if not isinstance(dotted_path, str):
            return dotted_path
        if dotted_path == "*":
            return source
        current: Any = source
        for segment in dotted_path.split("."):
            if isinstance(current, Mapping):
                current = current.get(segment)
            else:
                current = None
            if current is None:
                break
        return current

    def _run_node(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        blueprint: WorkflowBlueprint,
        handlers: Mapping[str, NodeHandler],
        node_results: Dict[str, NodeExecutionResult],
        active_subgraphs: Set[str],
    ) -> NodeExecutionResult:
        cache_key = self._cache_key(node, inputs)
        if node.cacheable and cache_key in self._memoized_results:
            return self._memoized_results[cache_key].model_copy(update={"cached": True})

        handler = handlers.get(node.node_id) or handlers.get(node.role)
        context = NodeExecutionContext(
            blueprint=blueprint,
            artifact_store=self.artifact_store,
            active_subgraphs=active_subgraphs,
            node_results=node_results,
            budget_remaining={},
        )
        try:
            result = handler(node, inputs, context) if handler is not None else self._default_handler(node, inputs)
        except Exception as exc:
            result = NodeExecutionResult(
                failure_type=FailureType.unknown,
                error=str(exc),
                trace={"exception": exc.__class__.__name__},
            )

        if node.cacheable and result.failure_type == FailureType.none:
            self._memoized_results[cache_key] = result
        return result

    def _default_handler(self, node: NodeSpec, inputs: JsonDict) -> NodeExecutionResult:
        input_text = json.dumps(inputs, ensure_ascii=True, sort_keys=True)
        tool_calls = len(node.tools) if node.kind == NodeKind.tool else 0
        approx_input_tokens = max(len(input_text) // 4, 1) if inputs else 1
        approx_output_tokens = 64 if node.kind in {NodeKind.agent, NodeKind.evaluator, NodeKind.router} else 16
        cost = ExecutionCost(
            usd=node.cost_hint_usd or 0.0,
            input_tokens=approx_input_tokens,
            output_tokens=approx_output_tokens,
            tool_calls=tool_calls,
            container_starts=1 if node.sandbox else 0,
        )
        outputs: JsonDict = {"result": {"node_id": node.node_id, "role": node.role}}
        if node.kind == NodeKind.tool and node.tools:
            outputs["tool"] = {"server": node.tools[0].server, "name": node.tools[0].tool}
        confidence = 0.7 if node.kind != NodeKind.router else 0.8
        return NodeExecutionResult(
            outputs=outputs,
            trace={"simulated": True, "kind": node.kind.value},
            cost=cost,
            confidence=confidence,
        )

    def _validate_input_contract(self, node: NodeSpec, inputs: JsonDict) -> list[ContractViolation]:
        return self._validate_schema(node.node_id, "input_schema", node.io.input_schema, inputs)

    def _validate_output_contract(self, node: NodeSpec, inputs: JsonDict, outputs: JsonDict) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        violations.extend(self._validate_schema(node.node_id, "output_schema", node.io.output_schema, outputs))
        for invariant in node.io.invariants:
            if not self._safe_eval(invariant, {"inputs": inputs, "outputs": outputs}):
                violations.append(
                    ContractViolation(
                        node_id=node.node_id,
                        contract_type="invariant",
                        message=f"Invariant failed: {invariant}",
                    )
                )
        return violations

    @staticmethod
    def _subgraphs_containing_nodes(blueprint: WorkflowBlueprint, node_ids: Set[str]) -> Set[str]:
        active: Set[str] = set()
        if not node_ids:
            return active
        for subgraph in blueprint.subgraphs:
            if any(node.node_id in node_ids for node in subgraph.nodes):
                active.add(subgraph.subgraph_id)
        return active

    @staticmethod
    def _edge_condition_state(edge: Any, node_results: Mapping[str, NodeExecutionResult]) -> Optional[bool]:
        if not getattr(edge, "condition", None):
            return True
        upstream = node_results.get(edge.src)
        env = {
            "node_results": {node_id: result.outputs for node_id, result in node_results.items()},
            "upstream": upstream.outputs if upstream is not None else None,
            "source": upstream.outputs if upstream is not None else None,
        }
        return BlueprintExecutor._safe_eval_optional(edge.condition, env)

    @staticmethod
    def _validate_schema(
        node_id: str,
        contract_type: str,
        schema: Optional[JsonDict],
        payload: JsonDict,
    ) -> list[ContractViolation]:
        if not schema or schema.get("type") != "object":
            return []
        required = schema.get("required", [])
        missing = [key for key in required if key not in payload]
        if not missing:
            return []
        return [
            ContractViolation(
                node_id=node_id,
                contract_type=contract_type,
                message=f"Missing required key '{key}'",
            )
            for key in missing
        ]

    def _run_hard_checks(self, blueprint: WorkflowBlueprint) -> list[HardCheckResult]:
        results: list[HardCheckResult] = []
        for check in blueprint.eval.hard_checks:
            env = os.environ.copy()
            if check.sandbox:
                env.update(check.sandbox.env)
            try:
                completed = subprocess.run(
                    check.cmd,
                    capture_output=True,
                    text=True,
                    timeout=check.timeout_s,
                    env=env,
                    cwd=check.sandbox.workdir if check.sandbox and check.sandbox.workdir else None,
                )
                passed = completed.returncode in check.expected_exit_codes
                failure_type = FailureType.none if passed else self.failure_classifier.classify_text(completed.stderr)
                results.append(
                    HardCheckResult(
                        check_id=check.check_id,
                        passed=passed,
                        exit_code=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                        failure_type=failure_type,
                    )
                )
            except subprocess.TimeoutExpired as exc:
                results.append(
                    HardCheckResult(
                        check_id=check.check_id,
                        passed=False,
                        stdout=exc.stdout or "",
                        stderr=exc.stderr or "",
                        failure_type=FailureType.timeout,
                    )
                )
            except (OSError, subprocess.SubprocessError) as exc:
                message = str(exc)
                results.append(
                    HardCheckResult(
                        check_id=check.check_id,
                        passed=False,
                        stderr=message,
                        failure_type=self.failure_classifier.classify_text(message),
                    )
                )
        return results

    def _run_soft_judges(
        self,
        blueprint: WorkflowBlueprint,
        traces: Sequence[NodeTrace],
    ) -> list[SoftJudgeResult]:
        if not blueprint.eval.soft_judges:
            return []
        confidences = [trace.confidence for trace in traces if trace.confidence is not None]
        base_confidence = sum(confidences) / len(confidences) if confidences else 0.5
        results: list[SoftJudgeResult] = []
        for judge in blueprint.eval.soft_judges:
            if judge.scale == "0-1":
                score = base_confidence
            elif judge.scale == "1-5":
                score = round(1 + base_confidence * 4, 2)
            else:
                score = round(1 + base_confidence * 9, 2)
            results.append(
                SoftJudgeResult(
                    judge_id=judge.judge_id,
                    score=score,
                    passed=score >= judge.pass_threshold,
                    raw_votes=[score] * judge.n_votes,
                    rationale="Deterministic proxy score derived from node confidence; replace with live judge models in production.",
                )
            )
        return results

    def _run_trace_assertions(
        self,
        assertions: Sequence[TraceAssertion],
        node_results: Mapping[str, NodeExecutionResult],
    ) -> list[ContractViolation]:
        summary = {
            "node_results": {node_id: result.outputs for node_id, result in node_results.items()},
            "tool_calls": sum(result.cost.tool_calls for result in node_results.values()),
        }
        violations: list[ContractViolation] = []
        for assertion in assertions:
            if not self._safe_eval(assertion.expr, {"trace": summary}):
                violations.append(
                    ContractViolation(
                        node_id="trace",
                        contract_type="invariant",
                        message=f"Trace assertion failed: {assertion.assertion_id}",
                    )
                )
        return violations

    def _maybe_activate_gates(
        self,
        *,
        blueprint: WorkflowBlueprint,
        report_context: Mapping[str, Any],
        active_subgraphs: Set[str],
        disabled_edges: Set[str],
        activated_gates: list[str],
        gate_activation_counts: Dict[str, int],
    ) -> None:
        for gate in blueprint.gates:
            if gate_activation_counts[gate.gate_id] >= gate.max_activations:
                continue
            if evaluate_trigger(gate.trigger, report_context):
                gate_activation_counts[gate.gate_id] += 1
                active_subgraphs.update(gate.enable_subgraphs)
                disabled_edges.update(gate.disable_edges)
                if gate.gate_id not in activated_gates:
                    activated_gates.append(gate.gate_id)

    @staticmethod
    def _aggregate_confidence(traces: Sequence[NodeTrace]) -> Optional[float]:
        values = [trace.confidence for trace in traces if trace.confidence is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    @staticmethod
    def _safe_eval(expr: str, env: Mapping[str, Any]) -> bool:
        value = BlueprintExecutor._safe_eval_optional(expr, env)
        return bool(value)

    @staticmethod
    def _safe_eval_optional(expr: str, env: Mapping[str, Any]) -> Optional[bool]:
        try:
            return bool(eval(expr, {"__builtins__": {}}, dict(env)))
        except Exception:
            return None

    @staticmethod
    def _cache_key(node: NodeSpec, inputs: JsonDict) -> str:
        return json.dumps(
            {
                "node_id": node.node_id,
                "inputs": inputs,
                "max_steps": node.max_steps,
                "model": node.model.model_dump() if node.model else None,
                "tools": [tool.model_dump() for tool in node.tools],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
