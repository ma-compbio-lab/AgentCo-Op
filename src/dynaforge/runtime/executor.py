from __future__ import annotations

import json
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Set

from dynaforge.ir.schema import FailureType, NodeKind, NodeSpec, PatchPlan, TraceAssertion, WorkflowBlueprint
from dynaforge.integrations.mcp_client import MCPClientManager
from dynaforge.integrations.sandbox import CompositeSandboxRunner, SandboxError, SandboxRunner
from dynaforge.integrations.tool_registry import ToolRegistry
from dynaforge.integrations.web_search import BUILTIN_WEB_SEARCH_SERVER, build_builtin_web_search_server_ref
from dynaforge.runtime.blame import BlameAssigner, FailureClassifier
from dynaforge.runtime.cache import ArtifactStore
from dynaforge.runtime.conditions import evaluate_trigger
from dynaforge.runtime.execution_context import NodeExecutionContext  # noqa: F401
from dynaforge.runtime.node_handlers import dispatch_node
from dynaforge.runtime.expression import evaluate_predicate
from dynaforge.runtime.llm import LLMClientError, LLMRouter
from dynaforge.runtime.patching import DeterministicPatchPolicy, apply_patch_plan, compute_impacted_nodes
from dynaforge.runtime.skills import SkillRegistry
from dynaforge.runtime.tool_scout import ToolScout
from dynaforge.runtime.reports import (
    BudgetLedger,
    ContractViolation,
    ExecutionCost,
    ExecutionReport,
    HardCheckResult,
    NodeExecutionResult,
    NodeTrace,
    SoftJudgeResult,
)
from dynaforge.runtime.validation import validate_json_payload
from dynaforge.workflows.expansion import RuntimeGraphExpansionPolicy

JsonDict = Dict[str, Any]


class NodeHandler(Protocol):
    def __call__(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
    ) -> NodeExecutionResult:
        ...


class BlueprintExecutor:
    """Executes a typed workflow blueprint and can apply local repair loops."""

    def __init__(
        self,
        artifact_root: str | Path = ".dynaforge_artifacts",
        *,
        patch_policy: Optional[DeterministicPatchPolicy] = None,
        blame_assigner: Optional[BlameAssigner] = None,
        llm_router: Optional[LLMRouter] = None,
        mcp_client: Optional[MCPClientManager] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_scout: Optional[ToolScout] = None,
        skill_registry: Optional[SkillRegistry] = None,
        sandbox_runner: Optional[SandboxRunner] = None,
        allow_offline_fallback: bool = True,
        runtime_expansion_policy: Optional[RuntimeGraphExpansionPolicy] = None,
    ):
        self.artifact_store = ArtifactStore(artifact_root)
        self.patch_policy = patch_policy or DeterministicPatchPolicy()
        self.blame_assigner = blame_assigner or BlameAssigner()
        self.failure_classifier = FailureClassifier()
        self.llm_router = llm_router or LLMRouter(allow_offline_fallback=allow_offline_fallback)
        self.mcp_client = mcp_client or MCPClientManager()
        self.tool_registry = tool_registry or ToolRegistry(self.mcp_client)
        self.tool_scout = tool_scout or ToolScout()
        self.skill_registry = skill_registry or SkillRegistry()
        self.sandbox_runner = sandbox_runner or CompositeSandboxRunner()
        self.runtime_expansion_policy = runtime_expansion_policy
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
        blueprint = self._augment_builtin_servers(blueprint)
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
        events: list[JsonDict] = []

        node_map = blueprint.node_map()
        executed: Set[str] = set()
        compile_trace = blueprint.meta.get("compile_trace")
        if isinstance(compile_trace, Mapping):
            events.append({"type": "compile_trace", "payload": dict(compile_trace)})

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
                inputs = self._assemble_inputs(blueprint, node_id, parent_map, active_edges, node_results)
                if self._should_skip_node(node, inputs):
                    result = NodeExecutionResult(
                        outputs={"skipped": True},
                        trace={"skipped_by_condition": str(node.meta.get("execution_condition", "") or "")},
                        confidence=1.0,
                    )
                    ledger.consume(result.cost)
                    node_results[node_id] = result
                    executed.add(node_id)
                    traces.append(
                        NodeTrace(
                            node_id=node.node_id,
                            role=node.role,
                            status="skipped",
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
                    events.append(
                        {
                            "type": "node_executed",
                            "node_id": node.node_id,
                            "role": node.role,
                            "status": "skipped",
                            "failure_type": result.failure_type.value,
                            "confidence": result.confidence,
                            "cost": result.cost.model_dump(),
                            "output_keys": sorted(result.outputs.keys()),
                        }
                    )
                    continue
                input_violations = self._validate_input_contract(node, inputs)
                if input_violations:
                    contract_violations.extend(input_violations)
                    result = NodeExecutionResult(
                        failure_type=FailureType.output_contract_violation,
                        error=f"Input contract validation failed at node {node_id}.",
                    )
                else:
                    result = self._run_node(
                        node,
                        inputs,
                        blueprint,
                        handlers,
                        node_results,
                        active_subgraphs,
                        ledger.snapshot().model_dump(),
                    )
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
                events.append(
                    {
                        "type": "node_executed",
                        "node_id": node.node_id,
                        "role": node.role,
                        "status": trace_status,
                        "failure_type": result.failure_type.value,
                        "confidence": result.confidence,
                        "cost": result.cost.model_dump(),
                        "output_keys": sorted(result.outputs.keys()),
                    }
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
                    events=events,
                )

                expansion_plan = self._maybe_expand_graph(
                    blueprint=blueprint,
                    node=node,
                    inputs=inputs,
                    result=result,
                    events=events,
                )
                if expansion_plan is not None:
                    impacted = compute_impacted_nodes(blueprint, expansion_plan)
                    blueprint = apply_patch_plan(blueprint, expansion_plan)
                    node_map = blueprint.node_map()
                    active_subgraphs.update(blueprint.default_active_subgraphs())
                    events.append(
                        {
                            "type": "runtime_graph_expansion",
                            "node_id": node.node_id,
                            "patch_plan": expansion_plan.model_dump(),
                            "impacted_nodes": sorted(impacted),
                        }
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
            events=events,
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
        blueprint: WorkflowBlueprint,
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
        for key, value in self._initial_task_payload(blueprint).items():
            payload.setdefault(key, value)
        return payload

    @staticmethod
    def _should_skip_node(node: NodeSpec, inputs: Mapping[str, Any]) -> bool:
        expression = str(node.meta.get("execution_condition", "") or "").strip()
        if not expression:
            return False
        return not evaluate_predicate(
            expression,
            {
                "inputs": dict(inputs),
                "task": dict(inputs.get("task", {})) if isinstance(inputs.get("task"), Mapping) else {},
                "workflow_meta": dict(inputs.get("workflow_meta", {}))
                if isinstance(inputs.get("workflow_meta"), Mapping)
                else {},
            },
            default_on_error=False,
        )

    @staticmethod
    def _initial_task_payload(blueprint: WorkflowBlueprint) -> JsonDict:
        return {
            "task": blueprint.task.model_dump(),
            "workflow_meta": blueprint.meta,
        }

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
        budget_remaining: Dict[str, Any],
    ) -> NodeExecutionResult:
        cache_key = self._cache_key(node, inputs)
        if node.cacheable and cache_key in self._memoized_results:
            return self._memoized_results[cache_key].model_copy(update={"cached": True})

        container_starts = 0
        if node.sandbox:
            was_materialized = self.sandbox_runner.is_materialized(node.sandbox.sandbox_id)
            try:
                self.sandbox_runner.ensure_materialized(node.sandbox)
                if not was_materialized:
                    container_starts = 1
            except SandboxError as exc:
                return NodeExecutionResult(
                    failure_type=FailureType.tool_runtime_error,
                    error=f"Sandbox materialization failed: {exc}",
                    trace={"sandbox_error": True, "sandbox_id": node.sandbox.sandbox_id},
                )

        handler = handlers.get(node.node_id) or handlers.get(node.role)
        context = NodeExecutionContext(
            blueprint=blueprint,
            artifact_store=self.artifact_store,
            active_subgraphs=active_subgraphs,
            node_results=node_results,
            budget_remaining=budget_remaining,
            llm_router=self.llm_router,
            mcp_client=self.mcp_client,
            tool_registry=self.tool_registry,
            tool_scout=self.tool_scout,
            skill_registry=self.skill_registry,
            sandbox_runner=self.sandbox_runner,
        )
        start_time = time.perf_counter()
        try:
            result = handler(node, inputs, context) if handler is not None else self._default_handler(node, inputs, context)
        except SandboxError as exc:
            result = NodeExecutionResult(
                failure_type=FailureType.tool_runtime_error,
                error=str(exc),
                trace={"exception": "SandboxError", "node_id": node.node_id},
            )
        except LLMClientError as exc:
            result = NodeExecutionResult(
                failure_type=FailureType.reasoning_inconsistency,
                error=str(exc),
                trace={"exception": "LLMClientError", "node_id": node.node_id},
            )
        except ValueError:
            raise  # From dispatch_node for unknown NodeKind — let it propagate
        except Exception as exc:
            result = NodeExecutionResult(
                failure_type=FailureType.unknown,
                error=str(exc),
                trace={"exception": exc.__class__.__name__, "node_id": node.node_id},
            )
        elapsed_s = max(0.0, time.perf_counter() - start_time)
        result.cost = result.cost.add(ExecutionCost(wall_time_s=round(elapsed_s, 6)))

        if container_starts:
            result.cost = result.cost.add(ExecutionCost(container_starts=container_starts))

        result = self._apply_output_policies(node, inputs, result)

        if node.cacheable and result.failure_type == FailureType.none:
            self._memoized_results[cache_key] = result
        return result

    def _default_handler(self, node: NodeSpec, inputs: JsonDict, context: NodeExecutionContext) -> NodeExecutionResult:
        return dispatch_node(node, inputs, context)

    def _validate_input_contract(self, node: NodeSpec, inputs: JsonDict) -> list[ContractViolation]:
        return self._validate_schema(node.node_id, "input_schema", node.io.input_schema, inputs)

    def _validate_output_contract(self, node: NodeSpec, inputs: JsonDict, outputs: JsonDict) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        violations.extend(self._validate_schema(node.node_id, "output_schema", node.io.output_schema, outputs))
        for invariant in node.io.invariants:
            if not evaluate_predicate(
                invariant,
                {"inputs": inputs, "outputs": outputs},
                default_on_error=False,
            ):
                violations.append(
                    ContractViolation(
                        node_id=node.node_id,
                        contract_type="invariant",
                        message=f"Invariant failed: {invariant}",
                    )
                )
        return violations

    def _apply_output_policies(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        result: NodeExecutionResult,
    ) -> NodeExecutionResult:
        if result.failure_type != FailureType.none or not isinstance(result.outputs, Mapping):
            return result

        outputs = dict(result.outputs)
        trace_updates: JsonDict = {}
        changed = False

        backfills = node.meta.get("output_backfills", [])
        if isinstance(backfills, Sequence) and not isinstance(backfills, (str, bytes)):
            applied_backfills: list[JsonDict] = []
            for spec in backfills:
                if not isinstance(spec, Mapping):
                    continue
                target = str(spec.get("target", "") or "").strip()
                if not target or self._has_nonempty_value(outputs.get(target)):
                    continue
                value, source_path = self._build_output_backfill(spec, inputs, outputs)
                if not self._has_nonempty_value(value):
                    continue
                outputs[target] = value
                applied_backfills.append(
                    {
                        "target": target,
                        "strategy": str(spec.get("strategy", "first_nonempty")),
                        "source": source_path,
                    }
                )
                changed = True
            if applied_backfills:
                trace_updates["output_backfills"] = applied_backfills

        change_guard = node.meta.get("output_change_guard")
        if isinstance(change_guard, Mapping):
            guard_trace = self._apply_output_change_guard(change_guard, inputs, outputs)
            if guard_trace is not None:
                trace_updates["output_change_guard"] = guard_trace
                changed = True

        if not changed:
            return result

        trace = dict(result.trace)
        trace.update(trace_updates)
        return result.model_copy(update={"outputs": outputs, "trace": trace})

    def _build_output_backfill(
        self,
        spec: Mapping[str, Any],
        inputs: Mapping[str, Any],
        outputs: Mapping[str, Any],
    ) -> tuple[Any, str]:
        strategy = str(spec.get("strategy", "first_nonempty") or "first_nonempty").strip().lower()
        source_paths = spec.get("sources")
        if isinstance(source_paths, Sequence) and not isinstance(source_paths, (str, bytes)):
            candidates = [str(item) for item in source_paths if str(item).strip()]
        else:
            source = str(spec.get("source", "") or "").strip()
            candidates = [source] if source else []

        for source_path in candidates:
            value = self._resolve_output_policy_value(source_path, inputs=inputs, outputs=outputs)
            if not self._has_nonempty_value(value):
                continue
            if strategy == "python_assignment_from_field":
                variable_name = str(spec.get("variable_name", "FINAL_ANSWER") or "FINAL_ANSWER").strip() or "FINAL_ANSWER"
                sentinel = str(spec.get("sentinel", "DYNaforge synthetic program fallback") or "").strip()
                header = f"# {sentinel}\n" if sentinel else ""
                assignment = json.dumps(value, ensure_ascii=False)
                return f"{header}{variable_name} = {assignment}\n", source_path
            return value, source_path
        return None, ""

    def _apply_output_change_guard(
        self,
        guard: Mapping[str, Any],
        inputs: Mapping[str, Any],
        outputs: Dict[str, Any],
    ) -> Optional[JsonDict]:
        output_path = str(guard.get("output_path", "") or "").strip()
        if not output_path:
            return None
        candidate = self._resolve_output_policy_value(output_path, inputs=inputs, outputs=outputs)
        record_field = str(guard.get("record_field", "output_changed") or "output_changed").strip() or "output_changed"
        reason_field = str(guard.get("reason_field", "output_change_reason") or "output_change_reason").strip() or "output_change_reason"
        normalize_mode = str(guard.get("normalize", "text") or "text").strip().lower()

        raw_compare = guard.get("compare_against", [])
        compare_paths = []
        if isinstance(raw_compare, Sequence) and not isinstance(raw_compare, (str, bytes)):
            compare_paths = [str(item) for item in raw_compare if str(item).strip()]
        elif isinstance(raw_compare, str) and raw_compare.strip():
            compare_paths = [raw_compare.strip()]

        normalized_candidate = self._normalize_guard_value(candidate, mode=normalize_mode)
        compared_to: list[str] = []
        changed = True
        reason = "candidate differs from all guarded references"
        for compare_path in compare_paths:
            reference = self._resolve_output_policy_value(compare_path, inputs=inputs, outputs=outputs)
            normalized_reference = self._normalize_guard_value(reference, mode=normalize_mode)
            if normalized_reference is None:
                continue
            compared_to.append(compare_path)
            if normalized_candidate == normalized_reference:
                changed = False
                reason = f"output matched guarded reference '{compare_path}'"
                break

        outputs[record_field] = changed
        outputs[reason_field] = reason
        return {
            "output_path": output_path,
            "normalize": normalize_mode,
            "compared_against": compared_to,
            "changed": changed,
            "reason": reason,
        }

    @staticmethod
    def _resolve_output_policy_value(
        path: str,
        *,
        inputs: Mapping[str, Any],
        outputs: Mapping[str, Any],
    ) -> Any:
        if not path:
            return None
        roots = []
        if path.startswith("inputs."):
            roots.append(("inputs", path.split("inputs.", 1)[1]))
        elif path.startswith("outputs."):
            roots.append(("outputs", path.split("outputs.", 1)[1]))
        else:
            roots.extend([("outputs", path), ("inputs", path)])

        for root_name, subpath in roots:
            current: Any = outputs if root_name == "outputs" else inputs
            if not subpath:
                return current
            for segment in subpath.split("."):
                if isinstance(current, Mapping):
                    current = current.get(segment)
                else:
                    current = None
                if current is None:
                    break
            if current is not None:
                return current
        return None

    @staticmethod
    def _has_nonempty_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    @staticmethod
    def _normalize_guard_value(value: Any, *, mode: str) -> Optional[str]:
        if value is None:
            return None
        text = str(value)
        if mode == "code":
            stripped = text.strip()
            if stripped.startswith("```") and stripped.endswith("```"):
                lines = stripped.splitlines()
                if len(lines) >= 3:
                    stripped = "\n".join(lines[1:-1]).strip()
            normalized_lines = [line.rstrip() for line in stripped.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
            return "\n".join(normalized_lines).strip()
        return text.strip()

    @staticmethod
    def _augment_builtin_servers(blueprint: WorkflowBlueprint) -> WorkflowBlueprint:
        needs_web_search = any(
            node.tool_discovery is not None and node.tool_discovery.include_web_search
            for node in blueprint.all_nodes()
        )
        if not needs_web_search:
            return blueprint
        if any(server.name == BUILTIN_WEB_SEARCH_SERVER for server in blueprint.mcp_servers):
            return blueprint
        return blueprint.model_copy(
            update={
                "mcp_servers": [*blueprint.mcp_servers, build_builtin_web_search_server_ref()],
            },
            deep=True,
        )

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
        return evaluate_predicate(edge.condition, env, default_on_error=None)

    @staticmethod
    def _validate_schema(
        node_id: str,
        contract_type: str,
        schema: Optional[JsonDict],
        payload: Any,
    ) -> list[ContractViolation]:
        if not schema:
            return []
        return [
            ContractViolation(
                node_id=node_id,
                contract_type=contract_type,
                message=message,
            )
            for message in validate_json_payload(schema, payload)
        ]

    def _run_hard_checks(self, blueprint: WorkflowBlueprint) -> list[HardCheckResult]:
        results: list[HardCheckResult] = []
        for check in blueprint.eval.hard_checks:
            try:
                if check.sandbox:
                    completed = self.sandbox_runner.run_in_sandbox(
                        check.sandbox,
                        check.cmd,
                        timeout_s=check.timeout_s,
                    )
                else:
                    completed = subprocess.run(
                        check.cmd,
                        capture_output=True,
                        text=True,
                        timeout=check.timeout_s,
                        env=os.environ.copy(),
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
            except SandboxError as exc:
                message = str(exc)
                results.append(
                    HardCheckResult(
                        check_id=check.check_id,
                        passed=False,
                        stderr=message,
                        failure_type=self.failure_classifier.classify_text(message),
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
            "node_costs": {node_id: result.cost.model_dump() for node_id, result in node_results.items()},
            "tool_calls": sum(result.cost.tool_calls for result in node_results.values()),
            "container_starts": sum(result.cost.container_starts for result in node_results.values()),
            "successful_nodes": sorted(
                node_id for node_id, result in node_results.items() if result.failure_type == FailureType.none
            ),
        }
        violations: list[ContractViolation] = []
        for assertion in assertions:
            if not evaluate_predicate(
                assertion.expr,
                {
                    "trace": summary,
                    "node_results": summary["node_results"],
                    "node_costs": summary["node_costs"],
                },
                default_on_error=False,
            ):
                violations.append(
                    ContractViolation(
                        node_id="trace",
                        contract_type="invariant",
                        message=f"Trace assertion failed: {assertion.assertion_id} ({assertion.expr})",
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
        events: list[JsonDict],
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
                events.append(
                    {
                        "type": "gate_activation",
                        "gate_id": gate.gate_id,
                        "enabled_subgraphs": list(gate.enable_subgraphs),
                        "disabled_edges": list(gate.disable_edges),
                    }
                )

    def _maybe_expand_graph(
        self,
        *,
        blueprint: WorkflowBlueprint,
        node: NodeSpec,
        inputs: JsonDict,
        result: NodeExecutionResult,
        events: list[JsonDict],
    ) -> Optional[PatchPlan]:
        if self.runtime_expansion_policy is None:
            return None
        current_expansions = sum(1 for event in events if event.get("type") == "runtime_graph_expansion")
        return self.runtime_expansion_policy.propose(
            blueprint=blueprint,
            node=node,
            inputs=inputs,
            result=result,
            expansion_count=current_expansions,
        )

    @staticmethod
    def _aggregate_confidence(traces: Sequence[NodeTrace]) -> Optional[float]:
        values = [trace.confidence for trace in traces if trace.confidence is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    @staticmethod
    def _cache_key(node: NodeSpec, inputs: JsonDict) -> str:
        return json.dumps(
            {
                "node_id": node.node_id,
                "role": node.role,
                "description": node.description,
                "inputs": inputs,
                "max_steps": node.max_steps,
                "system_prompt": node.system_prompt,
                "model": node.model.model_dump() if node.model else None,
                "tools": [tool.model_dump() for tool in node.tools],
                "tool_discovery": node.tool_discovery.model_dump() if node.tool_discovery else None,
                "skills": [skill.model_dump() for skill in node.skills],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
