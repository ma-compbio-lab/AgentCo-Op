import asyncio
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from core.contracts import ExecutionPlan, JudgeReport, Message, TaskSpec
from core.hooks import HookManager
from core.judge import Judge
from core.observability import Observability


def test_judge_and_aggregator_flow(monkeypatch):
    async def fake_run_agent(agent, *_args, **_kwargs):
        if agent == "judge":
            return SimpleNamespace(final_output=JudgeReport(ok=True, score=1.0))
        return SimpleNamespace(final_output="final")

    monkeypatch.setattr("core.judge.run_agent", fake_run_agent)

    with TemporaryDirectory() as tmpdir:
        judge = Judge(
            judge_agent="judge",
            aggregator_agent="aggregator",
            hooks=HookManager(),
            observability=Observability(log_dir=tmpdir),
        )
        task = TaskSpec(goal="go")
        plan = ExecutionPlan(protocol="pipeline", active_agents=["worker"], subtasks=[])
        state = {"messages": [Message(sender="worker", receiver="engine", content_type="text", content="out")]}
        report, answer = asyncio.run(judge.evaluate_and_summarize(task, plan, state, ctx=SimpleNamespace()))
        assert report.ok is True
        assert answer == "final"
