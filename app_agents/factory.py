from __future__ import annotations

from app_agents.io_agents import build_io_agent
from app_agents.judge import build_aggregator_agent, build_judge_agent
from app_agents.orchestrator import build_planner_agent
from app_agents.researcher import build_researcher_agent
from app_agents.spec_critic import build_spec_critic_agent
from app_agents.spec_designer import build_spec_designer_agent
from app_agents.tool_doc_synth import build_tool_doc_synth_agent
from app_agents.tool_evaluator import build_tool_evaluator_agent
from app_agents.tool_scout import build_tool_scout_agent
from app_agents.workers import build_worker_agent
from core.contracts import AgentSpec
from core.registry import Registry
from models import ModelRouting


def build_default_agents(routing: ModelRouting) -> tuple[dict[str, object], Registry]:
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
    judge_spec = AgentSpec(
        agent_id="judge",
        name="Judge",
        description="Evaluates outputs for compliance with criteria.",
        capabilities=["verify"],
        input_types=["text"],
        output_types=["json"],
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
    researcher_spec = AgentSpec(
        agent_id="researcher",
        name="Researcher",
        description="Performs web research and produces evidence packs.",
        capabilities=["research"],
        input_types=["text"],
        output_types=["json"],
    )
    spec_designer_spec = AgentSpec(
        agent_id="spec_designer",
        name="SpecDesigner",
        description="Designs constraints and success criteria for tasks.",
        capabilities=["spec"],
        input_types=["text"],
        output_types=["json"],
    )
    spec_critic_spec = AgentSpec(
        agent_id="spec_critic",
        name="SpecCritic",
        description="Reviews and refines task specifications.",
        capabilities=["spec"],
        input_types=["text"],
        output_types=["json"],
    )
    tool_scout_spec = AgentSpec(
        agent_id="tool_scout",
        name="ToolScout",
        description="Finds tools and repositories relevant to a task.",
        capabilities=["tool_search"],
        input_types=["text"],
        output_types=["json"],
    )
    tool_evaluator_spec = AgentSpec(
        agent_id="tool_evaluator",
        name="ToolEvaluator",
        description="Selects the best tool candidate.",
        capabilities=["tool_eval"],
        input_types=["text"],
        output_types=["json"],
    )
    tool_doc_synth_spec = AgentSpec(
        agent_id="tool_doc_synth",
        name="ToolDocSynth",
        description="Synthesizes runnable tool plans.",
        capabilities=["tool_plan"],
        input_types=["text"],
        output_types=["json"],
    )

    registry.register_agent(planner_spec)
    registry.register_agent(worker_spec)
    registry.register_agent(judge_spec)
    registry.register_agent(aggregator_spec)
    registry.register_agent(io_spec)
    registry.register_agent(researcher_spec)
    registry.register_agent(spec_designer_spec)
    registry.register_agent(spec_critic_spec)
    registry.register_agent(tool_scout_spec)
    registry.register_agent(tool_evaluator_spec)
    registry.register_agent(tool_doc_synth_spec)

    agent_pool["planner"] = build_planner_agent(routing.planner)
    agent_pool["worker"] = build_worker_agent(routing.worker)
    agent_pool["judge"] = build_judge_agent(routing.judge)
    agent_pool["aggregator"] = build_aggregator_agent(routing.aggregator)
    agent_pool["io_agent"] = build_io_agent(routing.worker)
    agent_pool["researcher"] = build_researcher_agent(routing.worker)
    agent_pool["spec_designer"] = build_spec_designer_agent(routing.planner)
    agent_pool["spec_critic"] = build_spec_critic_agent(routing.judge)
    agent_pool["tool_scout"] = build_tool_scout_agent(routing.worker)
    agent_pool["tool_evaluator"] = build_tool_evaluator_agent(routing.judge)
    agent_pool["tool_doc_synth"] = build_tool_doc_synth_agent(routing.planner)

    return agent_pool, registry
