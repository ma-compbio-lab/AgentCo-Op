from __future__ import annotations

from core.contracts import ExecutionPlan, JudgeReport, TaskSpec
from core.hooks import HookManager
from core.observability import Observability
from core.runtime import run_agent
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

        judge_prompt = build_judge_prompt(task, plan, state.get("messages", []))
        log_event("JUDGE", "start", "running judge", data={"plan_id": plan.plan_id})
        if should_show_prompts():
            log_event(
                "JUDGE",
                "prompt",
                "judge prompt preview",
                level="debug",
                data={"preview": clip_text(judge_prompt)},
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

        if not report.ok:
            return report, ""

        agg_prompt = build_aggregate_prompt(task, plan, state.get("messages", []))
        log_event("JUDGE", "aggregate", "running aggregator", data={"plan_id": plan.plan_id})
        if should_show_prompts():
            log_event(
                "JUDGE",
                "prompt",
                "aggregate prompt preview",
                level="debug",
                data={"preview": clip_text(agg_prompt)},
            )
        agg_result = await run_agent(
            self.aggregator_agent,
            agg_prompt,
            ctx,
            session=session,
            workflow_name="aggregate",
            max_turns=4,
            hooks=self.run_hooks,
        )
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
