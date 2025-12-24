from __future__ import annotations

from agents.io_agents import PassthroughIOAgent
from agents.reasoning_agents import LLMAgent
from core.contracts import AgentSpec
from core.registry import Registry


def build_default_agents(model_backend) -> tuple[dict[str, object], Registry]:
    registry = Registry()
    agent_pool: dict[str, object] = {}

    planner_spec = AgentSpec(
        agent_id="planner",
        name="Planner",
        description="Plans tasks and decomposes goals.",
        capabilities=["plan", "reason"],
        input_types=["text"],
        output_types=["text", "json"],
    )
    worker_spec = AgentSpec(
        agent_id="worker",
        name="Worker",
        description="Executes assigned tasks.",
        capabilities=["reason"],
        input_types=["text"],
        output_types=["text"],
    )
    verifier_spec = AgentSpec(
        agent_id="verifier",
        name="Verifier",
        description="Checks outputs for compliance with criteria.",
        capabilities=["verify"],
        input_types=["text"],
        output_types=["text"],
    )
    aggregator_spec = AgentSpec(
        agent_id="aggregator",
        name="Aggregator",
        description="Combines outputs into a final response.",
        capabilities=["aggregate"],
        input_types=["text"],
        output_types=["text"],
    )
    io_spec = AgentSpec(
        agent_id="io_agent",
        name="IOAgent",
        description="Normalizes inputs and outputs.",
        capabilities=["io"],
        input_types=["text", "json", "file_ref"],
        output_types=["text", "json"],
    )

    registry.register_agent(planner_spec)
    registry.register_agent(worker_spec)
    registry.register_agent(verifier_spec)
    registry.register_agent(aggregator_spec)
    registry.register_agent(io_spec)

    agent_pool["planner"] = LLMAgent(planner_spec, model_backend, role="planner")
    agent_pool["worker"] = LLMAgent(worker_spec, model_backend, role="worker")
    agent_pool["verifier"] = LLMAgent(verifier_spec, model_backend, role="verifier")
    agent_pool["aggregator"] = LLMAgent(aggregator_spec, model_backend, role="aggregator")
    agent_pool["io_agent"] = PassthroughIOAgent(io_spec, model_backend)

    return agent_pool, registry
