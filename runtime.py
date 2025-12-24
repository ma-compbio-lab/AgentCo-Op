from __future__ import annotations

from dataclasses import dataclass

from agents.factory import build_default_agents
from core.budget import BudgetRouter
from core.hooks import HookManager
from core.observability import Observability
from core.registry import Registry
from models import BaseModelBackend


@dataclass
class Runtime:
    model_backend: BaseModelBackend
    agent_pool: dict[str, object]
    registry: Registry
    hooks: HookManager
    observability: Observability
    budget_router: BudgetRouter


def build_runtime(model_backend: BaseModelBackend, log_dir: str) -> Runtime:
    agent_pool, registry = build_default_agents(model_backend)
    hooks = HookManager()
    observability = Observability(log_dir=log_dir)
    budget_router = BudgetRouter(default_model=model_backend.name)
    return Runtime(
        model_backend=model_backend,
        agent_pool=agent_pool,
        registry=registry,
        hooks=hooks,
        observability=observability,
        budget_router=budget_router,
    )
