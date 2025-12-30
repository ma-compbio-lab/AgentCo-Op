from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app_agents.factory import build_default_agents
from config import MemoryConfig, RepairConfig
from core.budget import BudgetRouter
from core.cache import Cache
from core.context import AppContext
from core.hooks import HookManager
from core.memory import Memory
from core.observability import Observability
from core.registry import Registry
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


def build_runtime(
    model_routing: ModelRouting,
    log_dir: str,
    repair: RepairConfig | None = None,
    memory_cfg: MemoryConfig | None = None,
) -> Runtime:
    from utils import log_event

    registry = Registry()
    agent_pool, registry = build_default_agents(model_routing)
    hooks = HookManager()
    observability = Observability(log_dir=log_dir)
    budget_router = BudgetRouter(model_routing)
    cache = Cache()
    safety = SafetyGate()

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
    context = AppContext(
        registry=registry,
        budget=budget_router,
        cache=cache,
        memory=memory,
        safety=safety,
        observability=observability,
        run_id=run_id,
        session_id=session_id,
    )
    log_event(
        "RUNTIME",
        "init",
        "runtime initialized",
        data={"run_id": run_id, "agents": list(agent_pool.keys())},
    )

    return Runtime(
        agent_pool=agent_pool,
        registry=registry,
        hooks=hooks,
        observability=observability,
        budget_router=budget_router,
        context=context,
        session=None,
        run_hooks=None,
        repair=repair or RepairConfig(),
    )
