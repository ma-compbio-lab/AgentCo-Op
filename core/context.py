from __future__ import annotations

from dataclasses import dataclass

from core.budget import BudgetRouter
from core.cache import Cache
from core.memory import Memory
from core.observability import Observability
from core.registry import Registry
from core.safety import SafetyGate


@dataclass
class AppContext:
    registry: Registry
    budget: BudgetRouter
    cache: Cache
    memory: Memory
    safety: SafetyGate
    observability: Observability
    run_id: str
    session_id: str
    docker_runtime: object | None = None
