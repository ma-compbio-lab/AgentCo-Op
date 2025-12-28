import asyncio
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from core.budget import BudgetRouter
from core.contracts import AgentSpec, TaskSpec
from core.hooks import HookManager
from core.observability import Observability
from core.orchestrator import Orchestrator
from core.registry import Registry
from models import ModelRouting


def test_orchestrator_fallback_plan_and_sanitization(monkeypatch):
    async def fake_run_agent(*_args, **_kwargs):
        return SimpleNamespace(final_output={"protocol": "bad_proto", "active_agents": ["ghost"], "subtasks": []})

    monkeypatch.setattr("core.orchestrator.run_agent", fake_run_agent)

    registry = Registry()
    registry.register_agent(
        AgentSpec(
            agent_id="worker",
            name="Worker",
            description="",
            capabilities=["reason"],
            input_types=["text"],
            output_types=["text"],
        )
    )
    routing = ModelRouting(planner="p", worker="w", judge="j", aggregator="a")
    budget = BudgetRouter(routing)
    hooks = HookManager()
    with TemporaryDirectory() as tmpdir:
        obs = Observability(log_dir=tmpdir)
        orch = Orchestrator(
            registry=registry,
            budget_router=budget,
            hooks=hooks,
            planner_agent=object(),
            observability=obs,
        )
        plan = asyncio.run(orch.plan(TaskSpec(goal="go"), ctx=SimpleNamespace()))

    assert plan.protocol == "pipeline"
    assert plan.active_agents == ["worker"]
    assert plan.subtasks[0].assigned_to == "worker"
