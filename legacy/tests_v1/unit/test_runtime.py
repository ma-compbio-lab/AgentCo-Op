from __future__ import annotations

from pathlib import Path

import pytest

from agentcoop.backends import MockLLM, default_registry
from agentcoop.core.compiler import compile_workflow
from agentcoop.core.profiler import profile_task
from agentcoop.core.runtime import RuntimeConfig, run_blueprint
from agentcoop.core.tracing import read_events
from agentcoop.skills import SkillRegistry

SKILLS = Path(__file__).resolve().parents[2] / "agentcoop" / "skills"


@pytest.mark.asyncio
async def test_gsm8k_blueprint_runs(tmp_path: Path) -> None:
    reg = SkillRegistry().load_dir(SKILLS)
    profile = profile_task("What is 13 * 17?").profile
    bp = compile_workflow(profile, reg)

    mock = MockLLM()
    mock.register("solver", {"final_answer": "221", "confidence": 0.9})
    mock.register("formatter", {"final_answer": "221", "confidence": 0.95})

    cfg = RuntimeConfig(backends=default_registry(llm_client=mock), run_root=str(tmp_path))
    out = await run_blueprint(bp, config=cfg, payload={"task": "13*17"})
    events = list(read_events(out.trace_path))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert out.final is not None
    assert out.final.output["final_answer"] == "221"


@pytest.mark.asyncio
async def test_code_blueprint_traverses_cycle(tmp_path: Path) -> None:
    reg = SkillRegistry().load_dir(SKILLS)
    profile = profile_task(
        "Implement reverse(s). Pass the unit tests."
    ).profile
    bp = compile_workflow(profile, reg)

    mock = MockLLM()
    mock.register("parser", {"task": "reverse_str", "confidence": 0.9})
    mock.register("programmer", {"code": "def reverse(s): return s[::-1]", "confidence": 0.85})
    mock.register("repair_planner", {"fix": "none", "confidence": 0.9})
    mock.register("formatter", {"final_answer": "def reverse(s): return s[::-1]", "confidence": 0.95})

    cfg = RuntimeConfig(backends=default_registry(llm_client=mock), run_root=str(tmp_path))
    out = await run_blueprint(bp, config=cfg, payload={"task": "reverse"})
    assert out.final is not None
    assert "final_answer" in out.final.output
