from __future__ import annotations

from core.contracts import ExecutionPlan, SubTask, TaskSpec
from core.registry import Registry
from core.budget import BudgetRouter
from core.hooks import HookManager


class Orchestrator:
    def __init__(self, registry: Registry, budget_router: BudgetRouter, hooks: HookManager) -> None:
        self.registry = registry
        self.budget_router = budget_router
        self.hooks = hooks

    def plan(self, task: TaskSpec) -> ExecutionPlan:
        task = self.hooks.pre_plan(task)

        candidates = self.registry.filter(required_caps=["reason"], input_types=task.input_modalities)
        if not candidates:
            raise ValueError("No agents available for the requested task input modalities.")

        protocol = "pipeline" if len(candidates) <= 3 else "roundtable"
        budget_decision = self.budget_router.choose(task.budget_tokens)

        planner = next((c for c in candidates if c.agent_id == "planner"), None)
        worker = next((c for c in candidates if c.agent_id == "worker"), None)

        subtasks: list[SubTask] = []
        if planner and worker and planner.agent_id != worker.agent_id:
            plan_task = SubTask(
                title="Plan task",
                instructions=f"Create a short plan for: {task.goal}",
                assigned_to=planner.agent_id,
            )
            execute_task = SubTask(
                title="Solve main task",
                instructions=task.goal,
                assigned_to=worker.agent_id,
                depends_on=[plan_task.sub_id],
            )
            subtasks.extend([plan_task, execute_task])
        else:
            primary = worker or planner or candidates[0]
            subtasks.append(
                SubTask(
                    title="Solve main task",
                    instructions=task.goal,
                    assigned_to=primary.agent_id,
                )
            )

        if len(subtasks) > 1:
            protocol = "pipeline"

        plan = ExecutionPlan(
            protocol=protocol,
            active_agents=[c.agent_id for c in candidates[:4]],
            subtasks=subtasks,
            budget_tokens=task.budget_tokens,
            model_hint=budget_decision.model_hint,
            acceptance_tests=task.success_criteria,
            max_rounds=6,
        )

        plan = self.hooks.post_plan(task, plan)
        return plan
