from __future__ import annotations

from core.contracts import ExecutionPlan, JudgeReport, TaskSpec
from core.hooks import HookManager
from core.observability import Observability
from core.runtime import run_agent, run_agent_streamed
from prompts import build_aggregate_prompt, build_judge_prompt


class Judge:
    def __init__(
        self,
        judge_agent,
        aggregator_agent,
        hooks: HookManager,
        observability: Observability,
        run_hooks: object | None = None,
    ) -> None:
        self.judge_agent = judge_agent
        self.aggregator_agent = aggregator_agent
        self.hooks = hooks
        self.observability = observability
        self.run_hooks = run_hooks

    async def evaluate_and_summarize(
        self, task: TaskSpec, plan: ExecutionPlan, state: dict, ctx, session=None
    ) -> tuple[JudgeReport, str]:
        from utils import clip_text, log_event, log_section, should_show_outputs, should_show_prompts

        log_section("JUDGE", "Evaluation")
        state = self.hooks.before_judge(task, plan, state)
        mcp_manager = getattr(ctx, "mcp_manager", None)
        judge_mcp_prompt = None
        agg_mcp_prompt = None
        if mcp_manager and mcp_manager.is_enabled():
            judge_mcp_prompt = await mcp_manager.get_prompt("judge")
            agg_mcp_prompt = await mcp_manager.get_prompt("aggregator")

        judge_prompt = build_judge_prompt(task, plan, state.get("messages", []), extra_instructions=judge_mcp_prompt)
        log_event("JUDGE", "start", "running judge", data={"plan_id": plan.plan_id})
        if should_show_prompts():
            log_event(
                "JUDGE",
                "prompt",
                "judge prompt preview",
                level="debug",
                data={"preview": clip_text(judge_prompt)},
            )
        judge_servers = []
        judge_tools = []
        judge_require_approval = False
        if plan and plan.meta:
            judge_servers = list(plan.meta.get("judge_mcp_servers", []) or [])
            judge_tools = list(plan.meta.get("judge_allowed_tools", []) or [])
            judge_require_approval = bool(plan.meta.get("judge_require_approval", False))

        if mcp_manager and mcp_manager.is_enabled() and judge_servers:
            try:
                async with mcp_manager.open_servers(
                    judge_servers,
                    allowed_tools=judge_tools,
                    require_approval=judge_require_approval,
                ) as mcp_servers:
                    with mcp_manager.bind_agent(self.judge_agent, mcp_servers, extra_tools=None):
                        judge_result = await run_agent(
                            self.judge_agent,
                            judge_prompt,
                            ctx,
                            session=session,
                            workflow_name="judge",
                            max_turns=6,
                            hooks=self.run_hooks,
                        )
            except PermissionError as exc:
                log_event(
                    "JUDGE",
                    "mcp_blocked",
                    "MCP approval required",
                    level="warn",
                    data={"error": str(exc)},
                )
                judge_result = await run_agent(
                    self.judge_agent,
                    judge_prompt,
                    ctx,
                    session=session,
                    workflow_name="judge",
                    max_turns=6,
                    hooks=self.run_hooks,
                )
        else:
            judge_result = await run_agent(
                self.judge_agent,
                judge_prompt,
                ctx,
                session=session,
                workflow_name="judge",
                max_turns=6,
                hooks=self.run_hooks,
            )
        report_obj = getattr(judge_result, "final_output", None)
        if isinstance(report_obj, dict):
            try:
                report = JudgeReport(**report_obj)
            except Exception:
                report = JudgeReport(ok=False, score=0.0, issues=["invalid_judge_output"])
        elif isinstance(report_obj, JudgeReport):
            report = report_obj
        else:
            report = JudgeReport(ok=False, score=0.0, issues=["invalid_judge_output"])
        self.observability.event_from_result("judge_result", judge_result, {"plan_id": plan.plan_id})
        log_event(
            "JUDGE",
            "report",
            "judge report ready",
            data={"ok": report.ok, "score": report.score, "issues": report.issues},
        )
        memory = getattr(ctx, "memory", None)
        if memory and memory.enabled and memory.store_judge_reports:
            # Persist a compact summary of success/failure for future planning.
            status = "success" if report.ok else "failure"
            note = [
                f"STATUS: {status}",
                f"TASK: {task.goal}",
                f"PROTOCOL: {getattr(plan, 'protocol', 'unknown')}",
                f"ISSUES: {', '.join(report.issues) if report.issues else 'None'}",
            ]
            if report.suggested_patch:
                note.append(f"PATCH: {report.suggested_patch}")
            memory.write_global(
                "\n".join(note),
                labels=[status, f"protocol:{getattr(plan, 'protocol', 'unknown')}"],
                category="event",
            )

        if not report.ok:
            return report, ""

        agg_prompt = build_aggregate_prompt(
            task,
            plan,
            state.get("messages", []),
            extra_instructions=agg_mcp_prompt,
        )
        log_event("JUDGE", "aggregate", "running aggregator", data={"plan_id": plan.plan_id})
        if should_show_prompts():
            log_event(
                "JUDGE",
                "prompt",
                "aggregate prompt preview",
                level="debug",
                data={"preview": clip_text(agg_prompt)},
            )
        agg_servers = []
        agg_tools = []
        agg_require_approval = False
        if plan and plan.meta:
            agg_servers = list(plan.meta.get("aggregator_mcp_servers", []) or [])
            agg_tools = list(plan.meta.get("aggregator_allowed_tools", []) or [])
            agg_require_approval = bool(plan.meta.get("aggregator_require_approval", False))

        async def _run_aggregator():
            if getattr(ctx, "stream_output", False) and getattr(ctx, "event_bus", None):
                return await run_agent_streamed(
                    self.aggregator_agent,
                    agg_prompt,
                    ctx,
                    session=session,
                    workflow_name="aggregate",
                    max_turns=4,
                    hooks=self.run_hooks,
                    event_bus=ctx.event_bus,
                )
            return await run_agent(
                self.aggregator_agent,
                agg_prompt,
                ctx,
                session=session,
                workflow_name="aggregate",
                max_turns=4,
                hooks=self.run_hooks,
            )

        if mcp_manager and mcp_manager.is_enabled() and agg_servers:
            try:
                async with mcp_manager.open_servers(
                    agg_servers,
                    allowed_tools=agg_tools,
                    require_approval=agg_require_approval,
                ) as mcp_servers:
                    with mcp_manager.bind_agent(self.aggregator_agent, mcp_servers, extra_tools=None):
                        agg_result = await _run_aggregator()
            except PermissionError as exc:
                log_event(
                    "JUDGE",
                    "mcp_blocked",
                    "MCP approval required",
                    level="warn",
                    data={"error": str(exc)},
                )
                agg_result = await _run_aggregator()
        else:
            agg_result = await _run_aggregator()
        final_answer = getattr(agg_result, "final_output", "")
        if not isinstance(final_answer, str):
            final_answer = str(final_answer)
        final_answer = self.hooks.before_output(task, plan, state, final_answer)
        self.observability.event_from_result("aggregate_result", agg_result, {"plan_id": plan.plan_id})
        log_event(
            "JUDGE",
            "done",
            "final answer ready",
            data={"output_len": len(final_answer)},
        )
        if should_show_outputs():
            log_event(
                "JUDGE",
                "output_preview",
                "final answer preview",
                level="debug",
                data={"preview": clip_text(final_answer)},
            )
        return report, final_answer
