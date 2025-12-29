from tempfile import TemporaryDirectory

from models import ModelRouting
from runtime import build_runtime


def test_build_runtime_and_agent_pool():
    routing = ModelRouting(planner="p", worker="w", judge="j", aggregator="a")
    with TemporaryDirectory() as tmpdir:
        runtime = build_runtime(routing, log_dir=tmpdir)
        assert "planner" in runtime.agent_pool
        assert "worker" in runtime.agent_pool
        assert "judge" in runtime.agent_pool
        assert runtime.context.registry is runtime.registry
        assert runtime.repair.enabled is True
