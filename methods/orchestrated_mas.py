from __future__ import annotations

from core.contracts import Message, TaskSpec
from core.judge import Judge
from core.orchestrator import Orchestrator
from core.runtime import run_agent
from core.tool_intelligence import prepare_tool_plan
from core.spec_enricher import ensure_spec
from engine.executor import ExecutionEngine
from engine.protocols import default_protocols
from methods.base import MethodResult
from runtime import Runtime
from prompts import build_repair_prompt, derive_output_template


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    from utils import clip_text, log_event, log_section, should_show_outputs, should_show_prompts

    log_section("METHOD", "Orchestrated")
    task = await ensure_spec(task, runtime.context, runtime.agent_pool, session=runtime.session)
    orchestrator = Orchestrator(
        registry=runtime.registry,
        budget_router=runtime.budget_router,
        hooks=runtime.hooks,
        planner_agent=runtime.agent_pool["planner"],
        observability=runtime.observability,
        run_hooks=runtime.run_hooks,
    )
    log_event("METHOD", "start", "orchestrated run", data={"task_id": task.task_id})
    plan = await orchestrator.plan(task, runtime.context, session=runtime.session)
    tool_plan = await prepare_tool_plan(
        task,
        runtime.context,
        runtime.agent_pool,
        session=runtime.session,
        run_hooks=runtime.run_hooks,
        tool_cfg=runtime.tool_cfg,
    )
    if tool_plan:
        plan.tool_plan = tool_plan
    template_sections = derive_output_template(task, runtime.repair.template_mode)
    if template_sections:
        if runtime.repair.template_mode == "force" or "output_template" not in plan.meta:
            plan.meta["output_template"] = template_sections

    engine = ExecutionEngine(
        agent_pool=runtime.agent_pool,
        protocols=default_protocols(),
        hooks=runtime.hooks,
        observability=runtime.observability,
        docker_runtime=runtime.docker_runtime,
        tool_cfg=runtime.tool_cfg,
        run_hooks=runtime.run_hooks,
    )
    state = await engine.run(task, plan, runtime.context, session=runtime.session)

    judge = Judge(
        judge_agent=runtime.agent_pool["judge"],
        aggregator_agent=runtime.agent_pool["aggregator"],
        hooks=runtime.hooks,
        observability=runtime.observability,
        run_hooks=runtime.run_hooks,
    )
    report, answer = await judge.evaluate_and_summarize(task, plan, state, runtime.context, session=runtime.session)
    repair_history: list[dict] = []
    evidence_artifacts = dict(state.get("artifacts") or {})
    evidence_messages = list(evidence_artifacts.get("evidence_messages") or [])
    # If the judge fails, run a bounded repair loop and re-evaluate.
    if not report.ok and runtime.repair.enabled and runtime.repair.max_rounds > 0:
        issues = list(report.issues)
        for attempt in range(1, runtime.repair.max_rounds + 1):
            log_section("REPAIR", f"Attempt {attempt}")
            log_event("REPAIR", "issues", "judge issues", data={"issues": issues})
            repair_prompt = build_repair_prompt(
                task,
                issues,
                state.get("messages", []),
                template_sections=plan.meta.get("output_template"),
                suggested_patch=report.suggested_patch,
            )
            if should_show_prompts():
                log_event(
                    "REPAIR",
                    "prompt",
                    "repair prompt preview",
                    level="debug",
                    data={"preview": clip_text(repair_prompt)},
                )
            repair_result = await run_agent(
                runtime.agent_pool["worker"],
                repair_prompt,
                runtime.context,
                session=runtime.session,
                workflow_name=f"repair:{attempt}",
                max_turns=plan.max_rounds,
                hooks=runtime.run_hooks,
            )
            runtime.observability.event_from_result("repair_result", repair_result, {"attempt": attempt})
            repair_text = getattr(repair_result, "final_output", "")
            if not isinstance(repair_text, str):
                repair_text = str(repair_text)
            if should_show_outputs():
                log_event(
                    "REPAIR",
                    "output",
                    "repair output preview",
                    level="debug",
                    data={"preview": clip_text(repair_text)},
                )
            repair_history.append({"attempt": attempt, "issues": issues, "output_len": len(repair_text)})
            state = {
                # Preserve evidence context across repair attempts.
                "messages": evidence_messages
                + [Message(sender="worker", receiver="engine", content_type="text", content=repair_text)],
                "artifacts": evidence_artifacts,
                "round": attempt,
                "repair_history": repair_history,
            }
            report, answer = await judge.evaluate_and_summarize(
                task, plan, state, runtime.context, session=runtime.session
            )
            if report.ok:
                break
            issues = list(report.issues)
    log_event(
        "METHOD",
        "done",
        "orchestrated complete",
        data={
            "ok": report.ok,
            "score": report.score,
            "output_len": len(answer),
            "repair_attempts": len(repair_history),
        },
    )
    return MethodResult(answer=answer, judge_report=report, state=state)
