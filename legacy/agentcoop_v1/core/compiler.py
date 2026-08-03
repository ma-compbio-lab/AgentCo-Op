"""Workflow compiler.

The compiler turns a `TaskProfile` + skill library into the simplest
sufficient `WorkflowBlueprint`. It does not search; it binds meta-skill
topology templates with agent-skills, attaches memory/gate/eval contracts,
and picks the minimum-complexity graph that still satisfies the hard
constraints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from agentcoop.core.schema import (
    AgentSkill,
    Budget,
    EdgeSpec,
    EvalContract,
    GatePolicy,
    MemoryPlan,
    MetaSkill,
    NodeSpec,
    TaskProfile,
    WorkflowBlueprint,
)
from agentcoop.skills.registry import SkillRegistry


# ----------------------------------------------------------------------------
# Complexity
# ----------------------------------------------------------------------------


def graph_complexity(nodes: list[NodeSpec], edges: list[EdgeSpec], gates: list[GatePolicy]) -> float:
    num_llm = sum(1 for n in nodes if n.backend == "llm")
    num_tool = sum(1 for n in nodes if n.backend in ("mcp", "python_sandbox"))
    num_sandbox = sum(1 for n in nodes if n.backend == "sandbox_repo")
    num_human = sum(1 for n in nodes if n.backend == "human_review")
    num_gates = len(gates)

    has_loop = _has_cycle(nodes, edges)
    has_parallel = _has_parallel(nodes, edges)

    return (
        0.10 * num_llm
        + 0.20 * num_tool
        + 0.35 * num_sandbox
        + 0.25 * num_gates
        + 0.40 * (1.0 if has_loop else 0.0)
        + 0.30 * (1.0 if has_parallel else 0.0)
        + 0.50 * num_human
    )


def _has_cycle(nodes: list[NodeSpec], edges: list[EdgeSpec]) -> bool:
    adj: dict[str, list[str]] = {n.node_id: [] for n in nodes}
    for e in edges:
        adj.setdefault(e.source, []).append(e.target)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in adj}

    def dfs(u: str) -> bool:
        color[u] = GRAY
        for v in adj.get(u, []):
            if color.get(v, WHITE) == GRAY:
                return True
            if color.get(v, WHITE) == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in color)


def _has_parallel(nodes: list[NodeSpec], edges: list[EdgeSpec]) -> bool:
    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    for e in edges:
        out_degree[e.source] = out_degree.get(e.source, 0) + 1
        in_degree[e.target] = in_degree.get(e.target, 0) + 1
    return any(v > 1 for v in out_degree.values()) or any(v > 1 for v in in_degree.values())


# ----------------------------------------------------------------------------
# Topology instantiation
# ----------------------------------------------------------------------------


def instantiate_topology(meta: MetaSkill, profile: TaskProfile) -> tuple[list[NodeSpec], list[EdgeSpec]]:
    template = meta.topology_template or {}
    node_templates = template.get("nodes", []) or []
    edge_templates = template.get("edges", []) or []

    nodes: list[NodeSpec] = []
    for nt in node_templates:
        if not isinstance(nt, dict):
            raise ValueError(f"{meta.name}: node template must be a dict, got {type(nt).__name__}")
        nid = nt.get("id") or nt.get("node_id")
        role = nt.get("role") or "specialist"
        backend = nt.get("backend") or "llm"
        nodes.append(
            NodeSpec(
                node_id=str(nid),
                role=str(role),
                backend=backend,
                memory_scope=nt.get("memory_scope", "private"),
                tool_policy=nt.get("tool_policy", {}),
                params=nt.get("params", {}),
                skill_refs=list(nt.get("skill_refs", [])),
            )
        )

    edges: list[EdgeSpec] = []
    for et in edge_templates:
        edges.append(
            EdgeSpec(
                source=str(et["source"]),
                target=str(et["target"]),
                condition=et.get("condition"),
                payload_map=et.get("payload_map", {}) or {},
            )
        )

    return nodes, edges


# ----------------------------------------------------------------------------
# Skill binding
# ----------------------------------------------------------------------------


def bind_agent_skills(
    nodes: list[NodeSpec],
    profile: TaskProfile,
    agents: Iterable[AgentSkill],
) -> list[NodeSpec]:
    """Attach agent-skill refs to nodes by role/backend + tag overlap.

    We keep this conservative: only attach when the skill's backend matches
    the node's backend and at least one tag overlaps with profile.domain.
    """
    agents = list(agents)
    out: list[NodeSpec] = []
    domain_tags = set(profile.domain)
    for node in nodes:
        refs: list[str] = list(node.skill_refs)
        for skill in agents:
            if skill.backend_type != node.backend:
                continue
            tags = set(skill.applicable_tags) | set(skill.capabilities)
            if not (tags & domain_tags) and skill.name not in refs:
                continue
            if skill.name not in refs:
                refs.append(skill.name)
        out.append(node.model_copy(update={"skill_refs": refs}))
    return out


# ----------------------------------------------------------------------------
# Gates / memory / eval
# ----------------------------------------------------------------------------


def attach_gate_policy(meta: MetaSkill, profile: TaskProfile) -> list[GatePolicy]:
    policies: list[GatePolicy] = []
    for g in meta.gates or []:
        if not isinstance(g, dict):
            continue
        policies.append(
            GatePolicy(
                name=str(g.get("name")),
                trigger=str(g.get("trigger", g.get("name"))),
                threshold=g.get("threshold"),
                action=str(g.get("action", "retry_node")),
                max_activations=int(g.get("max_activations", 1)),
                scope=g.get("scope", "node"),
                node_ids=list(g.get("node_ids", [])),
            )
        )
    # Always enforce a budget gate.
    if not any(p.name == "budget_near_limit" for p in policies):
        policies.append(
            GatePolicy(
                name="budget_near_limit",
                trigger="budget_near_limit",
                threshold=0.9,
                action="terminate_with_uncertainty",
                max_activations=1,
                scope="graph",
            )
        )
    return policies


def attach_memory_policy(meta: MetaSkill, profile: TaskProfile, nodes: list[NodeSpec]) -> MemoryPlan:
    return MemoryPlan(
        scratch_scope="per_node",
        blackboard_keys=[n.node_id for n in nodes],
        artifact_dir="artifacts",
        persist_trace=True,
    )


def attach_eval_contract(meta: MetaSkill, profile: TaskProfile) -> EvalContract:
    grader = None
    normalization = None
    if profile.verification_available == "unit_test":
        grader = "pytest"
        normalization = "pytest"
    elif profile.verification_available == "exact":
        grader = "exact_match"
        if "math" in profile.domain:
            normalization = "gsm8k_numeric"
        elif "qa" in profile.domain:
            normalization = "hotpotqa_em"
    elif profile.verification_available == "deterministic_grader":
        grader = "deterministic"
    elif profile.verification_available == "rubric":
        grader = "llm_rubric"

    required_evidence = "qa" in profile.domain or meta.requires_verification == "evidence_overlap"
    return EvalContract(
        answer_type=profile.answer_type,
        normalization=normalization,
        grader=grader,
        required_evidence=required_evidence,
        answer_schema=profile.output_schema,
    )


# ----------------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------------


def _required_coverage(profile: TaskProfile) -> float:
    # Lower bars for easy tasks so simple_direct_answer can win.
    by_difficulty = {
        "trivial": 0.25,
        "simple": 0.35,
        "moderate": 0.5,
        "complex": 0.55,
        "open_ended": 0.6,
    }
    base = by_difficulty.get(profile.difficulty, 0.5)
    if profile.repo_execution_need >= 0.5:
        base += 0.1
    if profile.retrieval_need >= 0.5:
        base += 0.05
    return base


def _capability_coverage(meta: MetaSkill, profile: TaskProfile) -> float:
    signals = meta.task_signals or {}
    score = 0.0
    domains = set(map(str, signals.get("domains") or []))
    if domains & set(profile.domain):
        score += 0.6
    if profile.answer_type in set(map(str, signals.get("answer_type") or [])):
        score += 0.2
    if profile.verification_available in set(map(str, signals.get("verification_available") or [])):
        score += 0.2
    return min(score, 1.0)


def _evidence_support(meta: MetaSkill) -> float:
    return 0.5 + 0.1 * min(len(meta.evidence_refs), 5)


def _verification_strength(meta: MetaSkill, profile: TaskProfile) -> float:
    if profile.verification_available in ("unit_test", "exact", "deterministic_grader"):
        return 1.0 if meta.requires_verification or meta.complexity_level >= 5 else 0.6
    return 0.4


def _risk_estimate(nodes: list[NodeSpec], profile: TaskProfile) -> float:
    risk = 0.0
    for n in nodes:
        if n.backend == "sandbox_repo":
            risk += 0.3
        if n.backend == "python_sandbox":
            risk += 0.05
        if n.backend == "human_review":
            risk += 0.1
    if profile.risk_level == "high":
        risk += 0.3
    elif profile.risk_level == "medium":
        risk += 0.1
    return risk


def _cost_estimate(nodes: list[NodeSpec], gates: list[GatePolicy]) -> float:
    # Per-node proxy cost; rough but enough to break ties.
    proxy = {"llm": 0.10, "mcp": 0.05, "python_sandbox": 0.02, "sandbox_repo": 0.30, "human_review": 0.00}
    return sum(proxy.get(n.backend, 0.05) for n in nodes) + 0.02 * len(gates)


def _satisfies_hard_constraints(
    nodes: list[NodeSpec],
    profile: TaskProfile,
    meta: MetaSkill,
) -> bool:
    if profile.repo_execution_need >= 0.5 and not any(n.backend == "sandbox_repo" for n in nodes):
        return False
    if profile.retrieval_need >= 0.5 and not any(
        n.backend == "mcp" or n.role == "retriever" for n in nodes
    ):
        return False
    # Inverse: don't pay for a retriever when the task doesn't need one.
    if profile.retrieval_need < 0.3 and any(
        n.backend == "mcp" or n.role == "retriever" for n in nodes
    ):
        return False
    if profile.answer_type == "program" and not any(
        n.backend == "python_sandbox" or n.role in ("programmer", "tool") for n in nodes
    ):
        return False
    if profile.risk_level == "high" and not any(n.backend == "human_review" for n in nodes):
        if not any((g.get("action") == "escalate_human") for g in (meta.gates or []) if isinstance(g, dict)):
            return False
    return True


_DIFFICULTY_TARGET_LEVEL: dict[str, int] = {
    "trivial": 0,
    "simple": 1,
    "moderate": 3,
    "complex": 6,
    "open_ended": 7,
}


def _score_candidate(
    meta: MetaSkill,
    nodes: list[NodeSpec],
    edges: list[EdgeSpec],
    gates: list[GatePolicy],
    profile: TaskProfile,
) -> tuple[float, float]:
    coverage = _capability_coverage(meta, profile)
    evidence = _evidence_support(meta)
    verify = _verification_strength(meta, profile)
    complexity = graph_complexity(nodes, edges, gates)
    cost = _cost_estimate(nodes, gates)
    risk = _risk_estimate(nodes, profile)
    score = coverage + 0.5 * evidence + 0.5 * verify - 0.7 * complexity - 0.3 * cost - 0.5 * risk
    score -= 0.2 * meta.complexity_penalty  # honor explicit preference

    # Simplicity-first: penalize topology levels above what the task needs.
    target_level = _DIFFICULTY_TARGET_LEVEL.get(profile.difficulty, 3)
    score -= 0.35 * max(0, meta.complexity_level - target_level)
    return score, complexity


@dataclass
class CompiledCandidate:
    blueprint: WorkflowBlueprint
    score: float
    complexity: float


def compile_workflow(
    profile: TaskProfile,
    registry: SkillRegistry,
    *,
    top_k_meta: int = 8,
    top_k_agents: int = 20,
    budget: Budget | None = None,
) -> WorkflowBlueprint:
    """Compile the minimal sufficient workflow for `profile`."""

    meta_candidates = registry.search_meta(profile, top_k=top_k_meta)
    if not meta_candidates:
        raise ValueError("no meta-skills available in registry")
    agent_candidates = registry.search_agents(profile, top_k=top_k_agents)

    scored: list[tuple[float, float, CompiledCandidate]] = []
    for meta in meta_candidates:
        try:
            nodes, edges = instantiate_topology(meta, profile)
        except ValueError:
            continue
        nodes = bind_agent_skills(nodes, profile, agent_candidates)
        gates = attach_gate_policy(meta, profile)
        memory_plan = attach_memory_policy(meta, profile, nodes)
        eval_contract = attach_eval_contract(meta, profile)

        if not _satisfies_hard_constraints(nodes, profile, meta):
            continue

        coverage = _capability_coverage(meta, profile)
        if coverage < _required_coverage(profile):
            continue

        score, complexity = _score_candidate(meta, nodes, edges, gates, profile)
        blueprint = WorkflowBlueprint(
            blueprint_id=f"bp-{uuid.uuid4().hex[:10]}",
            task_profile=profile,
            nodes=nodes,
            edges=edges,
            gate_policies=gates,
            memory_plan=memory_plan,
            eval_contract=eval_contract,
            budget=budget or profile.budget,
            topology_level=meta.complexity_level,
            complexity=complexity,
            provenance=[f"meta:{meta.name}"] + [f"agent:{s.name}" for s in agent_candidates if s.name in {ref for n in nodes for ref in n.skill_refs}],
        )
        scored.append((score, complexity, CompiledCandidate(blueprint, score, complexity)))

    if not scored:
        # Fallback: direct-answer topology with no agent bindings.
        return _fallback_direct_answer(profile, budget)

    # Maximize score; tie-break by smaller complexity.
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2].blueprint


def _fallback_direct_answer(profile: TaskProfile, budget: Budget | None) -> WorkflowBlueprint:
    solver = NodeSpec(node_id="solver", role="solver", backend="llm")
    formatter = NodeSpec(node_id="formatter", role="formatter", backend="llm")
    nodes = [solver, formatter]
    edges = [EdgeSpec(source="solver", target="formatter")]
    gates = [
        GatePolicy(
            name="budget_near_limit",
            trigger="budget_near_limit",
            threshold=0.9,
            action="terminate_with_uncertainty",
            max_activations=1,
            scope="graph",
        )
    ]
    return WorkflowBlueprint(
        blueprint_id=f"bp-fallback-{uuid.uuid4().hex[:8]}",
        task_profile=profile,
        nodes=nodes,
        edges=edges,
        gate_policies=gates,
        memory_plan=MemoryPlan(blackboard_keys=[n.node_id for n in nodes]),
        eval_contract=EvalContract(answer_type=profile.answer_type),
        budget=budget or profile.budget,
        topology_level=0,
        complexity=graph_complexity(nodes, edges, gates),
        provenance=["meta:fallback_direct_answer"],
    )


__all__ = [
    "compile_workflow",
    "instantiate_topology",
    "bind_agent_skills",
    "attach_gate_policy",
    "attach_memory_policy",
    "attach_eval_contract",
    "graph_complexity",
]
