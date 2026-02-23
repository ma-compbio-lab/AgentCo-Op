import asyncio
from types import SimpleNamespace

from config import AdaptiveConfig
from config import ToolConfig
from core.contracts import AdaptiveRoutingDecision
from core.contracts import TaskSpec
from methods import adaptive
from methods.base import MethodResult


def test_adaptive_routes_simple_task_to_baseline(monkeypatch):
    async def fake_decide_route(task, runtime):
        return (
            AdaptiveRoutingDecision(
                mode="single_agent",
                enable_tool_search=False,
                estimated_agents=1,
                confidence=0.9,
                reason="simple task",
            ),
            {"difficulty": "low", "task_type": "general"},
            "model",
        )

    async def fake_baseline_run(task, runtime):
        return MethodResult(
            answer="baseline",
            state={"messages": [SimpleNamespace(sender="worker")]},
        )

    async def fake_orchestrated_run(task, runtime):
        raise AssertionError("orchestrated should not be called for simple task")

    monkeypatch.setattr("methods.adaptive.decide_route", fake_decide_route)
    monkeypatch.setattr("methods.baseline.run", fake_baseline_run)
    monkeypatch.setattr("methods.orchestrated_mas.run", fake_orchestrated_run)

    runtime = SimpleNamespace(tool_cfg=ToolConfig(enabled=False))
    task = TaskSpec(
        goal="What is 2+2?",
        constraints=[],
        success_criteria=["Return one short answer."],
        allow_tool_search="off",
        task_type="general",
    )

    result = asyncio.run(adaptive.run(task, runtime))
    assert result.answer == "baseline"
    assert runtime.tool_cfg.enabled is False


def test_adaptive_routes_complex_task_keeps_tool_search_off_when_runtime_unavailable(monkeypatch):
    captured = {}

    async def fake_decide_route(task, runtime):
        return (
            AdaptiveRoutingDecision(
                mode="multi_agent",
                enable_tool_search=True,
                estimated_agents=4,
                confidence=0.82,
                reason="needs tools",
            ),
            {"difficulty": "high", "task_type": "analysis"},
            "model",
        )

    async def fake_baseline_run(task, runtime):
        raise AssertionError("baseline should not be called for complex task")

    async def fake_orchestrated_run(task, runtime):
        captured["allow_tool_search"] = task.allow_tool_search
        captured["tool_runtime_enabled_during_run"] = runtime.tool_cfg.enabled
        return MethodResult(
            answer="orchestrated",
            state={"messages": [SimpleNamespace(sender="planner"), SimpleNamespace(sender="worker")]},
        )

    monkeypatch.setattr("methods.adaptive.decide_route", fake_decide_route)
    monkeypatch.setattr("methods.baseline.run", fake_baseline_run)
    monkeypatch.setattr("methods.orchestrated_mas.run", fake_orchestrated_run)

    runtime = SimpleNamespace(tool_cfg=ToolConfig(enabled=False), docker_runtime=None)
    task = TaskSpec(
        goal="Benchmark this GitHub repo and use tools to run dockerized evaluation.",
        constraints=["Use an existing tool if available."],
        success_criteria=["Provide benchmark summary and tool plan."],
        allow_tool_search="auto",
        task_type="analysis",
    )

    result = asyncio.run(adaptive.run(task, runtime))
    assert result.answer == "orchestrated"
    assert captured["allow_tool_search"] == "auto"
    assert captured["tool_runtime_enabled_during_run"] is False
    assert runtime.tool_cfg.enabled is False


def test_adaptive_enables_tool_search_when_runtime_available(monkeypatch):
    captured = {}

    async def fake_decide_route(task, runtime):
        return (
            AdaptiveRoutingDecision(
                mode="multi_agent",
                enable_tool_search=True,
                estimated_agents=4,
                confidence=0.9,
                reason="tools available",
            ),
            {"difficulty": "high", "task_type": "analysis"},
            "model",
        )

    async def fake_orchestrated_run(task, runtime):
        captured["allow_tool_search"] = task.allow_tool_search
        captured["tool_runtime_enabled_during_run"] = runtime.tool_cfg.enabled
        return MethodResult(answer="ok", state={"messages": [SimpleNamespace(sender="worker")]})

    monkeypatch.setattr("methods.adaptive.decide_route", fake_decide_route)
    monkeypatch.setattr("methods.orchestrated_mas.run", fake_orchestrated_run)
    async def fake_baseline_run(task, runtime):
        raise AssertionError("baseline should not be called")

    monkeypatch.setattr("methods.baseline.run", fake_baseline_run)

    runtime = SimpleNamespace(tool_cfg=ToolConfig(enabled=True), docker_runtime=object())
    task = TaskSpec(
        goal="Run repository evaluation with available tool runtime.",
        constraints=["Use existing tools if helpful."],
        success_criteria=["Return validated output."],
        allow_tool_search="auto",
    )

    result = asyncio.run(adaptive.run(task, runtime))
    assert result.answer == "ok"
    assert captured["allow_tool_search"] == "on"
    assert captured["tool_runtime_enabled_during_run"] is True


def test_decide_route_applies_self_contained_guardrails(monkeypatch):
    async def fake_run_agent(*args, **kwargs):
        return SimpleNamespace(
            final_output=AdaptiveRoutingDecision(
                mode="single_agent",
                enable_tool_search=True,
                need_web_search=True,
                confidence=0.9,
                reason="model enabled tools",
            )
        )

    monkeypatch.setattr("methods.adaptive.run_agent", fake_run_agent)

    runtime = SimpleNamespace(
        adaptive_cfg=AdaptiveConfig(
            enabled=True,
            confidence_threshold=0.6,
            fallback_to_heuristic=True,
            router_model="gpt-5-nano-2025-08-07",
            enforce_self_contained_guardrails=True,
        ),
        tool_cfg=ToolConfig(enabled=True),
        budget_router=SimpleNamespace(model_for=lambda _role: "gpt-5-nano-2025-08-07"),
        context=SimpleNamespace(memory=SimpleNamespace(enabled=False), mcp_manager=None),
        session=None,
        run_hooks=None,
    )
    task = TaskSpec(
        goal=(
            "Question: Which option is correct?\n"
            "Options:\nA. One\nB. Two\nC. Three\nD. Four\n"
            "Answer with the option letter only."
        ),
        constraints=["Use only the provided options."],
        success_criteria=["Return one option letter."],
        allow_tool_search="auto",
        allow_web_search_for_spec="auto",
    )

    decision, _signals, source = asyncio.run(adaptive.decide_route(task, runtime))
    assert source == "model"
    assert decision.mode == "single_agent"
    assert decision.enable_tool_search is False
    assert decision.need_web_search is False


def test_decide_route_respects_force_mode():
    runtime = SimpleNamespace(
        adaptive_cfg=AdaptiveConfig(force_mode="single_agent"),
        tool_cfg=ToolConfig(enabled=False),
    )
    task = TaskSpec(
        goal="Any task",
        constraints=[],
        success_criteria=[],
    )

    decision, _signals, source = asyncio.run(adaptive.decide_route(task, runtime))
    assert decision.mode == "single_agent"
    assert decision.confidence == 1.0
    assert source == "forced"


def test_decide_route_low_confidence_falls_back(monkeypatch):
    async def fake_run_agent(*args, **kwargs):
        return SimpleNamespace(
            final_output=AdaptiveRoutingDecision(
                mode="single_agent",
                enable_tool_search=False,
                confidence=0.1,
                reason="uncertain model output",
            )
        )

    monkeypatch.setattr("methods.adaptive.run_agent", fake_run_agent)

    runtime = SimpleNamespace(
        adaptive_cfg=AdaptiveConfig(
            enabled=True,
            confidence_threshold=0.7,
            fallback_to_heuristic=True,
            router_model="gpt-5-nano-2025-08-07",
        ),
        tool_cfg=ToolConfig(enabled=False),
        budget_router=SimpleNamespace(model_for=lambda _role: "gpt-5-nano-2025-08-07"),
        context=SimpleNamespace(memory=SimpleNamespace(enabled=False)),
        session=None,
        run_hooks=None,
    )
    task = TaskSpec(
        goal="Benchmark a GitHub repo and report metrics",
        constraints=["Use existing tools if possible."],
        success_criteria=["Report accuracy and latency."],
        allow_tool_search="auto",
    )

    decision, signals, source = asyncio.run(adaptive.decide_route(task, runtime))
    assert source == "heuristic_low_confidence"
    assert decision.mode == "multi_agent"
    assert signals["difficulty"] in {"medium", "high"}
