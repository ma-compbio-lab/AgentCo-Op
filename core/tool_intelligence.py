from __future__ import annotations

import json
import re
from typing import Any

from config import ToolConfig
from core.contracts import TaskSpec, ToolCandidate, ToolCandidates, ToolPlan
from core.repo2run_runtime import Repo2RunRunner
from core.runtime import run_agent
from prompts import (
    build_tool_eval_prompt,
    build_tool_plan_prompt,
    build_tool_scout_prompt,
    format_memory_block,
)

_TOOL_SEARCH_KEYWORDS = (
    "library",
    "package",
    "pypi",
    "github",
    "repo",
    "repository",
    "cli",
    "tool",
    "install",
    "docker",
    "sdk",
)

_BLOCK_TOOL_TERMS = (
    "no external",
    "do not use external",
    "no dependencies",
    "without external",
)


def should_search_tools(task: TaskSpec) -> bool:
    return bool(tool_search_signals(task)["need_tool_search"])


def tool_search_signals(task: TaskSpec) -> dict[str, Any]:
    mode = (task.allow_tool_search or "auto").lower()
    hint = _extract_tool_hint(task)
    blocked = _constraints_block_tools(task.constraints)
    text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
    keyword_hits = [keyword for keyword in _TOOL_SEARCH_KEYWORDS if keyword in text]
    need_tool_search = False
    if mode == "on":
        need_tool_search = True
    elif mode == "auto":
        need_tool_search = not blocked and bool(keyword_hits or hint)
    return {
        "mode": mode,
        "hint": hint,
        "blocked_by_constraints": blocked,
        "keyword_hits": keyword_hits,
        "need_tool_search": need_tool_search,
    }


async def prepare_tool_plan(
    task: TaskSpec,
    ctx,
    agent_pool: dict[str, object],
    *,
    session=None,
    run_hooks=None,
    tool_cfg: ToolConfig | None = None,
) -> ToolPlan | None:
    from utils import clip_text, log_event, log_section, should_show_prompts

    tool_cfg = tool_cfg or ToolConfig()
    signals = tool_search_signals(task)
    hint = signals["hint"]

    if not tool_cfg.enabled:
        return None
    if tool_cfg.use_docker and getattr(ctx, "docker_runtime", None) is None:
        log_event(
            "TOOLS",
            "skip",
            "tool runtime unavailable; skip tool planning",
            level="warn",
            data={"use_docker": True},
        )
        return None
    if not hint and not signals["need_tool_search"]:
        return None

    if "tool_doc_synth" not in agent_pool:
        log_event("TOOLS", "skip", "tool_doc_synth agent not available", level="warn")
        return None

    cache_key = _tool_cache_key(task, hint)
    cache = getattr(ctx, "cache", None)
    cached_plan = _coerce_tool_plan(cache.get(cache_key) if cache else None)
    if cached_plan:
        log_event("TOOLS", "cache", "tool plan cache hit", data={"tool": cached_plan.selected_tool.name})
        return _apply_tool_defaults(cached_plan, tool_cfg)

    log_section("TOOLS", "Tool Discovery")
    memory_block = _build_memory_block(task, ctx)
    mcp_manager = getattr(ctx, "mcp_manager", None)
    scout_mcp_prompt = None
    eval_mcp_prompt = None
    plan_mcp_prompt = None
    if mcp_manager and mcp_manager.is_enabled():
        scout_mcp_prompt = await mcp_manager.get_prompt("tool_scout")
        eval_mcp_prompt = await mcp_manager.get_prompt("tool_evaluator")
        plan_mcp_prompt = await mcp_manager.get_prompt("tool_doc_synth")

    candidates: list[ToolCandidate] = []
    if hint:
        candidates = [hint]
    elif "tool_scout" in agent_pool:
        scout_prompt = build_tool_scout_prompt(
            task,
            memory_block=memory_block,
            extra_instructions=scout_mcp_prompt,
        )
        if should_show_prompts():
            log_event(
                "TOOLS",
                "prompt",
                "tool scout prompt preview",
                level="debug",
                data={"preview": clip_text(scout_prompt)},
            )
        scout_result = await run_agent(
            agent_pool["tool_scout"],
            scout_prompt,
            ctx,
            session=session,
            workflow_name="tool_scout",
            max_turns=6,
            hooks=run_hooks,
        )
        candidates = _coerce_candidates(getattr(scout_result, "final_output", None))

    if not candidates:
        log_event("TOOLS", "empty", "no tool candidates found", level="warn")
        return None

    max_candidates = max(1, task.tool_max_candidates)
    candidates = candidates[:max_candidates]
    selected = candidates[0]

    if len(candidates) > 1 and "tool_evaluator" in agent_pool:
        eval_prompt = build_tool_eval_prompt(
            task,
            candidates,
            memory_block=memory_block,
            extra_instructions=eval_mcp_prompt,
        )
        if should_show_prompts():
            log_event(
                "TOOLS",
                "prompt",
                "tool evaluator prompt preview",
                level="debug",
                data={"preview": clip_text(eval_prompt)},
            )
        eval_result = await run_agent(
            agent_pool["tool_evaluator"],
            eval_prompt,
            ctx,
            session=session,
            workflow_name="tool_eval",
            max_turns=4,
            hooks=run_hooks,
        )
        selected = _coerce_candidate(getattr(eval_result, "final_output", None)) or selected

    repo_container_spec = None
    if selected.kind == "github_repo" and tool_cfg.repo2run_enabled:
        repo_container_spec = _run_repo2run(selected, ctx, tool_cfg)
    plan_prompt = build_tool_plan_prompt(
        task,
        selected,
        memory_block=memory_block,
        extra_instructions=plan_mcp_prompt,
    )
    if should_show_prompts():
        log_event(
            "TOOLS",
            "prompt",
            "tool plan prompt preview",
            level="debug",
            data={"preview": clip_text(plan_prompt)},
        )
    plan_result = await run_agent(
        agent_pool["tool_doc_synth"],
        plan_prompt,
        ctx,
        session=session,
        workflow_name="tool_plan",
        max_turns=6,
        hooks=run_hooks,
    )
    plan = _coerce_tool_plan(getattr(plan_result, "final_output", None), fallback=selected)
    if not plan:
        log_event("TOOLS", "invalid", "tool plan output invalid", level="warn")
        return None

    if repo_container_spec:
        plan.container_spec = repo_container_spec

    plan = _apply_tool_defaults(plan, tool_cfg)
    if cache:
        cache.set(cache_key, plan)
    log_event(
        "TOOLS",
        "plan",
        "tool plan ready",
        data={"tool": plan.selected_tool.name, "install": plan.install_strategy},
    )
    return plan


def _build_memory_block(task: TaskSpec, ctx) -> str | None:
    memory = getattr(ctx, "memory", None)
    if not memory or not memory.enabled:
        return None
    recall = memory.recall_global(query=f"{task.goal} tool or repo plan", k=memory.top_k)
    return format_memory_block(recall, title="Tool Memory")


def _should_search_tools(task: TaskSpec) -> bool:
    return bool(tool_search_signals(task)["need_tool_search"])


def _constraints_block_tools(constraints: list[str]) -> bool:
    text = " ".join(constraints).lower()
    return any(term in text for term in _BLOCK_TOOL_TERMS)


def _extract_tool_hint(task: TaskSpec) -> ToolCandidate | None:
    text = " ".join([task.goal, *task.constraints, *task.success_criteria])
    match = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text)
    if match:
        org, repo = match.groups()
        url = f"https://github.com/{org}/{repo}"
        return ToolCandidate(kind="github_repo", name=f"{org}/{repo}", repo_url=url)
    match = re.search(r"pip install ([A-Za-z0-9_.-]+)", text)
    if match:
        name = match.group(1)
        return ToolCandidate(kind="pypi", name=name)
    return None


def _tool_cache_key(task: TaskSpec, hint: ToolCandidate | None) -> str:
    payload = {
        "goal": task.goal,
        "constraints": task.constraints,
        "criteria": task.success_criteria,
        "hint": hint.model_dump(mode="json") if hint else None,
    }
    return f"tool_plan:{json.dumps(payload, sort_keys=True, ensure_ascii=True)}"


def _coerce_candidates(obj: Any) -> list[ToolCandidate]:
    if isinstance(obj, ToolCandidates):
        return obj.candidates
    if isinstance(obj, list):
        out: list[ToolCandidate] = []
        for item in obj:
            candidate = _coerce_candidate(item)
            if candidate:
                out.append(candidate)
        return out
    if isinstance(obj, dict):
        payload = dict(obj)
        raw = payload.get("candidates") or payload.get("items")
        if isinstance(raw, list):
            return _coerce_candidates(raw)
        candidate = _coerce_candidate(payload)
        return [candidate] if candidate else []
    return []


def _coerce_candidate(obj: Any) -> ToolCandidate | None:
    if isinstance(obj, ToolCandidate):
        return obj
    if isinstance(obj, dict):
        try:
            payload = _normalize_candidate_payload(obj)
            return ToolCandidate(**payload)
        except Exception:
            return None
    return None


def _coerce_tool_plan(obj: Any, fallback: ToolCandidate | None = None) -> ToolPlan | None:
    if isinstance(obj, ToolPlan):
        return obj
    if isinstance(obj, dict):
        try:
            payload = dict(obj)
            if "selected_tool" in payload and isinstance(payload["selected_tool"], dict):
                payload["selected_tool"] = _normalize_candidate_payload(payload["selected_tool"])
            if "selected_tool" not in payload and fallback is not None:
                payload["selected_tool"] = fallback.model_dump(mode="json")
            container = payload.get("container_spec")
            if container is None:
                payload["container_spec"] = {}
            elif isinstance(container, dict) and container.get("limits") is None:
                container = dict(container)
                container["limits"] = {}
                payload["container_spec"] = container
            return ToolPlan(**payload)
        except Exception:
            return None
    return None


def _normalize_candidate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    kind = str(normalized.get("kind", "")).strip().lower()
    if kind in {"repo", "repository", "github", "gh"}:
        normalized["kind"] = "github_repo"
    if kind in {"package", "python_package"}:
        normalized["kind"] = "pypi"
    return normalized


def _apply_tool_defaults(plan: ToolPlan, tool_cfg: ToolConfig) -> ToolPlan:
    spec = plan.container_spec
    if not spec.base_image:
        spec.base_image = tool_cfg.base_image
    spec.build_allow_net = spec.build_allow_net and tool_cfg.build_allow_net
    spec.run_allow_net = spec.run_allow_net and tool_cfg.run_allow_net
    if spec.limits.cpus is None:
        spec.limits.cpus = tool_cfg.default_cpus
    if spec.limits.memory_mb is None:
        spec.limits.memory_mb = tool_cfg.default_memory_mb
    if spec.limits.pids is None:
        spec.limits.pids = tool_cfg.default_pids
    if spec.limits.timeout_s is None:
        spec.limits.timeout_s = tool_cfg.default_timeout_s
    return plan


def _run_repo2run(candidate: ToolCandidate, ctx, tool_cfg: ToolConfig):
    from utils import log_event

    runner = Repo2RunRunner(
        repo2run_path=tool_cfg.repo2run_path,
        work_dir=tool_cfg.repo2run_work_dir,
        python_exe=tool_cfg.repo2run_python,
        llm=tool_cfg.repo2run_llm,
        prefer_existing=tool_cfg.repo2run_prefer_existing,
        observability=getattr(ctx, "observability", None),
    )
    result = runner.prepare(candidate)
    if not result:
        log_event("TOOLS", "repo2run", "repo2run preparation failed", level="warn")
        return None
    log_event(
        "TOOLS",
        "repo2run",
        "repo2run dockerfile ready",
        data={"dockerfile": result.dockerfile_path},
    )
    return runner.to_container_spec(result)
