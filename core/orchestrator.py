from __future__ import annotations

from core.contracts import EvidenceRequest, ExecutionPlan, SubTask, TaskSpec
from core.budget import BudgetRouter
from core.hooks import HookManager
from core.observability import Observability
from core.registry import Registry
from core.runtime import run_agent
from prompts import build_plan_prompt


class Orchestrator:
    def __init__(
        self,
        registry: Registry,
        budget_router: BudgetRouter,
        hooks: HookManager,
        planner_agent,
        observability: Observability,
        run_hooks: object | None = None,
    ) -> None:
        self.registry = registry
        self.budget_router = budget_router
        self.hooks = hooks
        self.planner_agent = planner_agent
        self.observability = observability
        self.run_hooks = run_hooks

    async def plan(self, task: TaskSpec, ctx, session=None) -> ExecutionPlan:
        from utils import clip_text, log_event, log_section, should_show_prompts

        log_section("ORCH", "Planning")
        task = self.hooks.pre_plan(task)

        candidates = self.registry.filter(required_caps=["reason"], input_types=task.input_modalities)
        candidates = [c for c in candidates if c.agent_id not in {"planner", "judge", "aggregator"}]
        if not candidates:
            raise ValueError("No agents available for the requested task input modalities.")

        prompt = build_plan_prompt(task, candidates)
        log_event("ORCH", "input", "planning input prepared", data={"candidates": [c.agent_id for c in candidates]})
        if should_show_prompts():
            log_event(
                "ORCH",
                "prompt",
                "plan prompt preview",
                level="debug",
                data={"preview": clip_text(prompt)},
            )
        result = await run_agent(
            self.planner_agent,
            prompt,
            ctx,
            session=session,
            workflow_name="plan",
            max_turns=6,
            hooks=self.run_hooks,
        )
        plan_obj = getattr(result, "final_output", None)
        if isinstance(plan_obj, dict):
            try:
                plan = ExecutionPlan(**plan_obj)
            except Exception:
                plan = self._fallback_plan(task, candidates)
        elif isinstance(plan_obj, ExecutionPlan):
            plan = plan_obj
        else:
            plan = self._fallback_plan(task, candidates)

        candidate_ids = {c.agent_id for c in candidates}
        if plan.active_agents:
            plan.active_agents = [agent_id for agent_id in plan.active_agents if agent_id in candidate_ids]
        if not plan.active_agents:
            plan.active_agents = [c.agent_id for c in candidates[:4]]
        if not plan.subtasks:
            plan.subtasks = [SubTask(title="Solve main task", instructions=task.goal, assigned_to=candidates[0].agent_id)]
        else:
            primary = plan.active_agents[0]
            for subtask in plan.subtasks:
                if subtask.assigned_to not in candidate_ids:
                    subtask.assigned_to = primary
        if plan.protocol not in {"pipeline", "roundtable", "debate", "loop", "hybrid"}:
            plan.protocol = "pipeline"

        plan = self._apply_evidence_defaults(task, plan)
        budget_decision = self.budget_router.choose(task.budget_tokens)
        if plan.model_hint is None:
            plan.model_hint = budget_decision.model_hint
        plan.meta["budget_reason"] = budget_decision.reason

        plan.acceptance_tests = plan.acceptance_tests or task.success_criteria
        plan = self.hooks.post_plan(task, plan)
        self.observability.event_from_result("plan_result", result, {"plan_id": plan.plan_id})
        log_event(
            "ORCH",
            "plan",
            "execution plan ready",
            data={
                "protocol": plan.protocol,
                "active_agents": plan.active_agents,
                "subtasks": [s.title for s in plan.subtasks],
            },
        )
        log_event(
            "ORCH",
            "plan_json",
            "plan details",
            level="debug",
            data=plan.model_dump(mode="json"),
        )
        return plan

    def _fallback_plan(self, task: TaskSpec, candidates: list) -> ExecutionPlan:
        budget_decision = self.budget_router.choose(task.budget_tokens)
        return ExecutionPlan(
            protocol="pipeline",
            active_agents=[c.agent_id for c in candidates[:4]],
            subtasks=[SubTask(title="Solve main task", instructions=task.goal, assigned_to=candidates[0].agent_id)],
            budget_tokens=task.budget_tokens,
            acceptance_tests=task.success_criteria,
            max_rounds=4,
            model_hint=budget_decision.model_hint,
            meta={"budget_reason": budget_decision.reason},
        )

    def _apply_evidence_defaults(self, task: TaskSpec, plan: ExecutionPlan) -> ExecutionPlan:
        needs_evidence = plan.needs_web_search or bool(plan.evidence_requests)
        if self._should_use_web_search(task):
            needs_evidence = True
        if needs_evidence and not plan.evidence_requests:
            plan.evidence_requests = [EvidenceRequest(query=task.goal)]
        if plan.evidence_requests and not plan.needs_web_search:
            plan.needs_web_search = True
        return plan

    @staticmethod
    def _should_use_web_search(task: TaskSpec) -> bool:
        text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
        keywords = (
            "latest",
            "recent",
            "today",
            "current",
            "new",
            "news",
            "2024",
            "2025",
            "citation",
            "reference",
            "source",
            "web",
            "internet",
            "policy",
            "price",
            "release",
            "report",
        )
        return any(keyword in text for keyword in keywords)
