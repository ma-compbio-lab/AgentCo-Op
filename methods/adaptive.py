from __future__ import annotations

from typing import Any

from agents import Agent
from agents.agent_output import AgentOutputSchema

from config import AdaptiveConfig
from core.contracts import AdaptiveRoutingDecision, RoutingDimension, TaskSpec
from core.runtime import run_agent
from core.tool_intelligence import tool_search_signals
from methods.base import MethodResult
from prompts import build_adaptive_route_prompt, format_memory_block
from runtime import Runtime
from utils import estimate_tokens


def _infer_task_type(task: TaskSpec) -> str:
    explicit = (task.task_type or "auto").strip().lower()
    if explicit != "auto":
        return explicit
    text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
    if any(k in text for k in ("implement", "function", "code", "python", "fix", "test")):
        return "coding"
    if any(k in text for k in ("benchmark", "dataset", "analysis", "metric", "evaluate")):
        return "analysis"
    if any(k in text for k in ("research", "citation", "source", "latest", "policy")):
        return "research"
    return "general"


def _needs_background_knowledge(task: TaskSpec) -> bool:
    text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
    keywords = (
        "medical",
        "clinical",
        "policy",
        "regulation",
        "legal",
        "finance",
        "law",
        "guideline",
        "domain knowledge",
        "benchmark",
    )
    return any(word in text for word in keywords)


def _has_eval_metrics(task: TaskSpec) -> bool:
    text = " ".join(task.success_criteria).lower()
    metric_words = (
        "accuracy",
        "f1",
        "precision",
        "recall",
        "auc",
        "rouge",
        "bleu",
        "pass@",
        "latency",
        "throughput",
        "benchmark",
        "error rate",
        "top-",
    )
    return any(word in text for word in metric_words)


def _needs_fresh_web_knowledge(task: TaskSpec) -> bool:
    if task.allow_web_search_for_spec == "on":
        return True
    text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
    freshness_words = ("latest", "today", "current", "newest", "recent", "2025", "2026", "version")
    return any(word in text for word in freshness_words)


def _collect_memory_signals(task: TaskSpec, runtime: Runtime, cfg: AdaptiveConfig) -> tuple[dict[str, Any], str | None]:
    memory = getattr(runtime.context, "memory", None)
    if not memory or not memory.enabled:
        return {
            "enabled": False,
            "records": 0,
            "success_hits": 0,
            "failure_hits": 0,
            "has_prior_solution_pattern": False,
            "has_unresolved_failure_pattern": False,
        }, None

    recall = memory.recall_global(
        query=f"{task.goal} successful solution pattern and failure analysis",
        k=max(1, cfg.memory_top_k),
    )
    success_hits = 0
    failure_hits = 0
    for item in recall:
        text = (item.get("text") or "").lower()
        if any(token in text for token in ("success", "solved", "fixed", "passed", "resolved")):
            success_hits += 1
        if any(token in text for token in ("fail", "failed", "error", "bug", "unresolved", "regression")):
            failure_hits += 1
    signals = {
        "enabled": True,
        "records": len(recall),
        "success_hits": success_hits,
        "failure_hits": failure_hits,
        "has_prior_solution_pattern": success_hits > 0,
        "has_unresolved_failure_pattern": failure_hits > success_hits,
    }
    return signals, format_memory_block(recall, title="Routing Memory")


def _difficulty_bucket(task: TaskSpec, *, tool_need: bool, metrics_needed: bool, knowledge_needed: bool) -> str:
    text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
    score = 0
    score += int(estimate_tokens(text) > 220)
    score += int(len(task.constraints) >= 3)
    score += int(len(task.success_criteria) >= 3)
    score += int(tool_need)
    score += int(metrics_needed)
    score += int(knowledge_needed)
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _heuristic_fallback(task: TaskSpec, signals: dict[str, Any]) -> AdaptiveRoutingDecision:
    tool_need = bool(signals["tool"]["need_tool_search"])
    difficulty = signals["difficulty"]
    mode = "single_agent" if difficulty == "low" and not tool_need else "multi_agent"
    reason = (
        f"heuristic fallback: difficulty={difficulty}, "
        f"tool_need={tool_need}, "
        f"metrics={signals['metrics_needed']}, "
        f"knowledge={signals['knowledge_needed']}"
    )
    dimensions = [
        RoutingDimension(
            name="difficulty",
            value=difficulty,
            impact="high" if difficulty == "high" else "medium",
            rationale="Computed from task size, constraints, criteria, tool/knowledge/metrics signals.",
        ),
        RoutingDimension(
            name="tool_need",
            value=str(tool_need).lower(),
            impact="high" if tool_need else "low",
            rationale="Derived from explicit toggle, constraints, keyword hits, and tool hints.",
        ),
        RoutingDimension(
            name="memory_history",
            value=(
                "unresolved_failures"
                if signals["memory"]["has_unresolved_failure_pattern"]
                else "prior_success_or_none"
            ),
            impact="medium",
            rationale="Memory indicates whether similar unresolved failures exist.",
        ),
    ]
    return AdaptiveRoutingDecision(
        mode=mode,
        enable_tool_search=tool_need,
        need_web_search=signals["web_freshness_needed"],
        estimated_agents=1 if mode == "single_agent" else 4,
        confidence=0.65,
        reason=reason,
        recommended_protocol="pipeline" if mode == "multi_agent" else None,
        dimensions=dimensions,
    )


def _build_router_agent(model_name: str) -> Agent:
    return Agent(
        name="AdaptiveRouter",
        instructions=(
            "You are a routing controller. Decide whether to use single-agent baseline or "
            "multi-agent orchestration. Return AdaptiveRoutingDecision JSON only."
        ),
        model=model_name,
        output_type=AgentOutputSchema(AdaptiveRoutingDecision, strict_json_schema=False),
    )


def _coerce_decision(obj: Any) -> AdaptiveRoutingDecision | None:
    if isinstance(obj, AdaptiveRoutingDecision):
        return obj
    if isinstance(obj, dict):
        try:
            return AdaptiveRoutingDecision(**obj)
        except Exception:
            return None
    return None


def _collect_signals(task: TaskSpec, runtime: Runtime, cfg: AdaptiveConfig) -> tuple[dict[str, Any], str | None]:
    tool = tool_search_signals(task)
    metrics_needed = _has_eval_metrics(task)
    knowledge_needed = _needs_background_knowledge(task)
    memory_signals, memory_block = _collect_memory_signals(task, runtime, cfg)
    difficulty = _difficulty_bucket(
        task,
        tool_need=bool(tool["need_tool_search"]),
        metrics_needed=metrics_needed,
        knowledge_needed=knowledge_needed,
    )
    mcp_manager = getattr(runtime.context, "mcp_manager", None)
    mcp_servers = mcp_manager.server_names() if mcp_manager and mcp_manager.is_enabled() else []
    signals = {
        "task_type": _infer_task_type(task),
        "task_tokens_est": estimate_tokens(" ".join([task.goal, *task.constraints, *task.success_criteria])),
        "constraints_count": len(task.constraints),
        "success_criteria_count": len(task.success_criteria),
        "difficulty": difficulty,
        "knowledge_needed": knowledge_needed,
        "metrics_needed": metrics_needed,
        "web_freshness_needed": _needs_fresh_web_knowledge(task),
        "tool": {
            "need_tool_search": bool(tool["need_tool_search"]),
            "blocked_by_constraints": bool(tool["blocked_by_constraints"]),
            "keyword_hits": list(tool["keyword_hits"]),
            "hint_name": tool["hint"].name if tool.get("hint") else None,
            "hint_kind": tool["hint"].kind if tool.get("hint") else None,
            "runtime_enabled": bool(runtime.tool_cfg.enabled),
            "mcp_servers": mcp_servers,
        },
        "memory": memory_signals,
        "has_ambiguous_spec": not task.constraints or not task.success_criteria,
    }
    return signals, memory_block


async def decide_route(task: TaskSpec, runtime: Runtime) -> tuple[AdaptiveRoutingDecision, dict[str, Any], str]:
    cfg = getattr(runtime, "adaptive_cfg", AdaptiveConfig())
    forced_mode = (cfg.force_mode or "auto").strip().lower()
    if forced_mode in {"single_agent", "multi_agent"}:
        forced = AdaptiveRoutingDecision(
            mode=forced_mode,
            enable_tool_search=forced_mode == "multi_agent",
            estimated_agents=1 if forced_mode == "single_agent" else 4,
            confidence=1.0,
            reason=f"forced by adaptive.force_mode={forced_mode}",
            dimensions=[RoutingDimension(name="force_mode", value=forced_mode, impact="high")],
        )
        return forced, {"forced": True}, "forced"

    signals, memory_block = _collect_signals(task, runtime, cfg)
    heuristic = _heuristic_fallback(task, signals)
    if not cfg.enabled:
        return heuristic, signals, "heuristic_disabled"

    router_model = cfg.router_model or runtime.budget_router.model_for("planner")
    router = _build_router_agent(router_model)
    prompt = build_adaptive_route_prompt(task, signals, memory_block=memory_block)

    try:
        result = await run_agent(
            router,
            prompt,
            runtime.context,
            session=runtime.session,
            workflow_name="adaptive_router",
            max_turns=max(1, cfg.max_turns),
            hooks=runtime.run_hooks,
        )
        model_decision = _coerce_decision(getattr(result, "final_output", None))
    except Exception:
        model_decision = None

    if model_decision is None:
        return heuristic, signals, "heuristic_parse_fallback"

    if cfg.fallback_to_heuristic and model_decision.confidence < cfg.confidence_threshold:
        return heuristic, signals, "heuristic_low_confidence"

    # Safety rails: preserve strong deterministic signals when model under-specifies.
    if signals["tool"]["need_tool_search"]:
        model_decision.enable_tool_search = True
    if model_decision.mode == "single_agent" and signals["difficulty"] == "high":
        model_decision.mode = "multi_agent"
        model_decision.estimated_agents = max(2, model_decision.estimated_agents)
        model_decision.notes.append("Escalated to multi_agent due to high deterministic difficulty.")
    if not model_decision.reason:
        model_decision.reason = "model decision applied"
    return model_decision, signals, "model"


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    from utils import log_event, log_section
    from methods import baseline as baseline_method
    from methods import orchestrated_mas as orchestrated_method

    log_section("METHOD", "Adaptive")
    decision, signals, source = await decide_route(task, runtime)

    run_task = task
    updates: dict[str, Any] = {}
    if decision.enable_tool_search and task.allow_tool_search != "on":
        updates["allow_tool_search"] = "on"
    if decision.need_web_search and task.allow_web_search_for_spec == "auto":
        updates["allow_web_search_for_spec"] = "on"
    if updates:
        run_task = task.model_copy(update=updates)

    original_tool_enabled = runtime.tool_cfg.enabled
    tool_runtime_used = decision.enable_tool_search
    if decision.enable_tool_search and not original_tool_enabled:
        runtime.tool_cfg.enabled = True

    log_event(
        "METHOD",
        "route",
        "adaptive route decision",
        data={
            "mode": decision.mode,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "decision_source": source,
            "estimated_agents": decision.estimated_agents,
            "tool_search": run_task.allow_tool_search,
            "web_search": run_task.allow_web_search_for_spec,
            "tool_runtime_enabled": runtime.tool_cfg.enabled,
            "difficulty": signals.get("difficulty"),
            "task_type": signals.get("task_type"),
        },
    )

    try:
        if decision.mode == "single_agent":
            result = await baseline_method.run(run_task, runtime)
        else:
            result = await orchestrated_method.run(run_task, runtime)
    finally:
        runtime.tool_cfg.enabled = original_tool_enabled

    state = result.state or {}
    messages = state.get("messages", []) if isinstance(state, dict) else []
    active_senders = sorted({getattr(msg, "sender", "unknown") for msg in messages})
    log_event(
        "METHOD",
        "mode_status",
        "adaptive execution finished",
        data={
            "mode": decision.mode,
            "decision_source": source,
            "confidence": decision.confidence,
            "active_agents": len(active_senders) or decision.estimated_agents,
            "agent_names": active_senders or (["worker"] if decision.mode == "single_agent" else []),
            "tool_use": bool(tool_runtime_used),
        },
    )
    return result
