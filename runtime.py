from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app_agents.factory import build_default_agents
from config import AdaptiveConfig, ExecConfig, MCPConfig, MemoryConfig, PlanningConfig, RepairConfig, ToolConfig
from core.budget import BudgetRouter
from core.cache import Cache
from core.context import AppContext
from core.docker_runtime import DockerRuntime
from core.hooks import HookManager
from core.local_exec import LocalExecutor, WriteApproval
from core.memory import Memory
from core.observability import Observability
from core.observability_hooks import EventStreamHooks
from core.planning_memory import PlanningMemory
from core.registry import Registry
from core.mcp_manager import MCPManager
from core.safety import SafetyGate
from models import ModelRouting


@dataclass
class Runtime:
    agent_pool: dict[str, object]
    registry: Registry
    hooks: HookManager
    observability: Observability
    budget_router: BudgetRouter
    context: AppContext
    session: object | None
    run_hooks: object | None
    repair: RepairConfig
    planning_cfg: PlanningConfig
    tool_cfg: ToolConfig
    exec_cfg: ExecConfig
    mcp_cfg: MCPConfig
    adaptive_cfg: AdaptiveConfig
    docker_runtime: DockerRuntime | None


def build_runtime(
    model_routing: ModelRouting,
    log_dir: str,
    repair: RepairConfig | None = None,
    memory_cfg: MemoryConfig | None = None,
    planning_cfg: PlanningConfig | None = None,
    tool_cfg: ToolConfig | None = None,
    exec_cfg: ExecConfig | None = None,
    mcp_cfg: MCPConfig | None = None,
    adaptive_cfg: AdaptiveConfig | None = None,
) -> Runtime:
    from utils import log_event

    registry = Registry()
    agent_pool, registry = build_default_agents(model_routing)
    hooks = HookManager()
    observability = Observability(log_dir=log_dir)
    budget_router = BudgetRouter(model_routing)
    cache = Cache()
    safety = SafetyGate()
    tool_cfg = tool_cfg or ToolConfig()
    exec_cfg = exec_cfg or ExecConfig()
    mcp_cfg = mcp_cfg or MCPConfig()
    adaptive_cfg = adaptive_cfg or AdaptiveConfig()
    planning_cfg = planning_cfg or PlanningConfig()
    docker_runtime = None
    if tool_cfg.enabled and tool_cfg.use_docker:
        docker_runtime = DockerRuntime(
            log_dir=log_dir,
            run_dir=tool_cfg.run_dir,
            default_base_image=tool_cfg.base_image,
            default_limits={
                "cpus": tool_cfg.default_cpus,
                "memory_mb": tool_cfg.default_memory_mb,
                "pids": tool_cfg.default_pids,
                "timeout_s": tool_cfg.default_timeout_s,
            },
            build_allow_net=tool_cfg.build_allow_net,
            run_allow_net=tool_cfg.run_allow_net,
            observability=observability,
            hooks=hooks,
        )

    local_executor = None
    if exec_cfg.enabled:
        # Local execution is opt-in and gated by workspace-only write checks.
        approval = WriteApproval(Path(exec_cfg.workspace_root).resolve())
        local_executor = LocalExecutor(exec_cfg, approval)

    run_id = str(uuid4())
    session_id = run_id
    memory = Memory(
        enabled=bool(memory_cfg.enabled) if memory_cfg else False,
        db_path=memory_cfg.db_path if memory_cfg else "memory/agent_cop.db",
        entity_id=memory_cfg.entity_id if memory_cfg else "default",
        session_id=session_id,
        top_k=memory_cfg.top_k if memory_cfg else 3,
        store_agent_outputs=memory_cfg.store_agent_outputs if memory_cfg else True,
        store_judge_reports=memory_cfg.store_judge_reports if memory_cfg else True,
        auto_build=memory_cfg.auto_build if memory_cfg else False,
    )
    mcp_manager = MCPManager(mcp_cfg)
    planning_memory = PlanningMemory(planning_cfg, memory=memory)
    if planning_memory.enabled:
        hooks.register_pre_plan(lambda task: _planning_pre_plan(planning_memory, task))
        hooks.register_post_plan(lambda task, plan: _planning_post_plan(planning_memory, task, plan))
        hooks.register_pre_step(lambda task, plan, state, step: _planning_pre_step(planning_memory, task, plan, state, step))
        hooks.register_post_step(lambda task, plan, state, output_msg: _planning_post_step(planning_memory, task, plan, state, output_msg))
        hooks.register_before_judge(lambda task, plan, state: _planning_before_judge(planning_memory, task, plan, state))
        hooks.register_before_output(lambda task, plan, state, final_answer: _planning_before_output(planning_memory, task, plan, state, final_answer))
        hooks.register_on_tool_error(lambda task, plan, state, error: _planning_on_tool_error(planning_memory, task, error))

    context = AppContext(
        registry=registry,
        budget=budget_router,
        cache=cache,
        memory=memory,
        planning_memory=planning_memory,
        safety=safety,
        observability=observability,
        run_id=run_id,
        session_id=session_id,
        docker_runtime=docker_runtime,
        local_executor=local_executor,
        mcp_manager=mcp_manager,
    )
    log_event(
        "RUNTIME",
        "init",
        "runtime initialized",
        data={"run_id": run_id, "agents": list(agent_pool.keys())},
    )
    if mcp_manager.is_enabled():
        log_event(
            "RUNTIME",
            "mcp",
            "MCP enabled",
            data={"servers": mcp_manager.server_names()},
        )

    return Runtime(
        agent_pool=agent_pool,
        registry=registry,
        hooks=hooks,
        observability=observability,
        budget_router=budget_router,
        context=context,
        session=None,
        # Always attach lifecycle hooks so tool activity (e.g., web search) is visible in logs.
        run_hooks=EventStreamHooks(),
        repair=repair or RepairConfig(),
        planning_cfg=planning_cfg,
        tool_cfg=tool_cfg,
        exec_cfg=exec_cfg,
        mcp_cfg=mcp_cfg,
        adaptive_cfg=adaptive_cfg,
        docker_runtime=docker_runtime,
    )


def _planning_pre_plan(planning_memory: PlanningMemory, task):
    planning_memory.ensure_task(task)
    planning_memory.mark_phase(task, "Phase 1: Requirements & Discovery", "in_progress")
    planning_memory.add_progress(task, "Initialized planning memory")
    return task


def _planning_post_plan(planning_memory: PlanningMemory, task, plan):
    planning_memory.mark_phase(task, "Phase 1: Requirements & Discovery", "complete")
    planning_memory.mark_phase(task, "Phase 2: Planning & Structure", "in_progress")
    planning_memory.add_decision(task, "Protocol", getattr(plan, "protocol", "unknown"))
    planning_memory.add_decision(task, "Active agents", ", ".join(getattr(plan, "active_agents", []) or []))
    planning_memory.add_progress(task, f"Plan created with {len(getattr(plan, 'subtasks', []) or [])} subtasks")
    return plan


def _planning_pre_step(planning_memory: PlanningMemory, task, plan, state, step):
    planning_memory.mark_phase(task, "Phase 2: Planning & Structure", "complete")
    planning_memory.mark_phase(task, "Phase 3: Implementation", "in_progress")
    instructions = step.get("instructions") if isinstance(step, dict) else ""
    if instructions:
        planning_memory.add_progress(task, f"Step start: {instructions[:160]}")
    return step


def _planning_post_step(planning_memory: PlanningMemory, task, plan, state, output_msg):
    summary = getattr(output_msg, "content", "")
    if summary:
        planning_memory.add_progress(task, f"Step output: {str(summary)[:160]}")
    return None


def _planning_before_judge(planning_memory: PlanningMemory, task, plan, state):
    planning_memory.mark_phase(task, "Phase 3: Implementation", "complete")
    planning_memory.mark_phase(task, "Phase 4: Testing & Verification", "in_progress")
    planning_memory.add_progress(task, "Judge evaluation started")
    return state


def _planning_before_output(planning_memory: PlanningMemory, task, plan, state, final_answer: str):
    planning_memory.mark_phase(task, "Phase 4: Testing & Verification", "complete")
    planning_memory.mark_phase(task, "Phase 5: Delivery", "in_progress")
    planning_memory.add_progress(task, "Final answer assembled")
    planning_memory.mark_phase(task, "Phase 5: Delivery", "complete")
    return final_answer


def _planning_on_tool_error(planning_memory: PlanningMemory, task, error):
    if not task:
        return
    planning_memory.add_error(task, str(error))
