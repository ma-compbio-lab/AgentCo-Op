import asyncio
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from config import RepairConfig
from core.contracts import ExecutionPlan, JudgeReport, Message, SubTask, TaskSpec
from core.hooks import HookManager
from core.observability import Observability
from methods import orchestrated_mas


def test_orchestrated_repair_loop_injects_issues(monkeypatch):
    plan = ExecutionPlan(
        protocol="pipeline",
        active_agents=["worker"],
        subtasks=[SubTask(title="t1", instructions="do", assigned_to="worker")],
    )

    async def fake_plan(_self, _task, _ctx, session=None):
        return plan

    async def fake_engine_run(_self, _task, _plan, _ctx, session=None):
        return {"messages": [Message(sender="worker", receiver="engine", content_type="text", content="bad")]}

    call_state = {"count": 0}

    async def fake_judge_eval(_self, _task, _plan, state, _ctx, session=None):
        call_state["count"] += 1
        if call_state["count"] == 1:
            return JudgeReport(ok=False, score=0.0, issues=["Missing tests"]), ""
        assert any("fixed" in msg.content for msg in state.get("messages", []))
        return JudgeReport(ok=True, score=1.0, issues=[]), "final answer"

    captured = {}

    async def fake_run_agent(_agent, prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(final_output="fixed output")

    monkeypatch.setattr("core.orchestrator.Orchestrator.plan", fake_plan)
    monkeypatch.setattr("engine.executor.ExecutionEngine.run", fake_engine_run)
    monkeypatch.setattr("core.judge.Judge.evaluate_and_summarize", fake_judge_eval)
    monkeypatch.setattr("methods.orchestrated_mas.run_agent", fake_run_agent)

    with TemporaryDirectory() as tmpdir:
        runtime = SimpleNamespace(
            registry=object(),
            budget_router=object(),
            hooks=HookManager(),
            agent_pool={"planner": object(), "worker": object(), "judge": object(), "aggregator": object()},
            observability=Observability(log_dir=tmpdir),
            context=SimpleNamespace(session_id="s", run_id="r"),
            session=None,
            run_hooks=None,
            repair=RepairConfig(enabled=True, max_rounds=1, template_mode="off"),
        )
        task = TaskSpec(goal="go", constraints=["c1"], success_criteria=["s1"])
        result = asyncio.run(orchestrated_mas.run(task, runtime))

    assert result.answer == "final answer"
    assert "Missing tests" in captured["prompt"]
    assert call_state["count"] == 2
