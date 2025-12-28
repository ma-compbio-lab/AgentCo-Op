from core.budget import BudgetRouter
from core.contracts import AgentSpec
from core.registry import Registry
from models import ModelRouting


def test_registry_filter_by_caps_and_input():
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
    registry.register_agent(
        AgentSpec(
            agent_id="io",
            name="IO",
            description="",
            capabilities=["io"],
            input_types=["file_ref"],
            output_types=["json"],
        )
    )
    matches = registry.filter(required_caps=["reason"], input_types=["text"])
    assert [m.agent_id for m in matches] == ["worker"]


def test_budget_router_model_for_roles():
    routing = ModelRouting(planner="p", worker="w", judge="j", aggregator="a")
    router = BudgetRouter(routing)
    assert router.model_for("planner") == "p"
    assert router.model_for("worker") == "w"
    assert router.model_for("judge") == "j"
    assert router.model_for("aggregator") == "a"
