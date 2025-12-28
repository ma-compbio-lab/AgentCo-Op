import asyncio
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from core.contracts import ExecutionPlan, SubTask, TaskSpec
from core.hooks import HookManager
from core.observability import Observability
from engine.executor import ExecutionEngine
from engine.protocols import PipelineProtocol


def test_executor_runs_and_falls_back_to_valid_agent(monkeypatch):
    async def fake_run_agent(*_args, **_kwargs):
        return SimpleNamespace(final_output="ok", context_wrapper=SimpleNamespace(usage={"tokens": 1}))

    monkeypatch.setattr("engine.executor.run_agent", fake_run_agent)

    plan = ExecutionPlan(
        protocol="pipeline",
        active_agents=["worker"],
        subtasks=[SubTask(title="t1", instructions="do", assigned_to="missing")],
    )
    with TemporaryDirectory() as tmpdir:
        engine = ExecutionEngine(
            agent_pool={"worker": object()},
            protocols={"pipeline": PipelineProtocol()},
            hooks=HookManager(),
            observability=Observability(log_dir=tmpdir),
        )
        state = asyncio.run(engine.run(TaskSpec(goal="go"), plan, ctx=SimpleNamespace()))
        assert state["messages"][0].sender == "worker"
