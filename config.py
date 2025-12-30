from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskConfig:
    goal: str = "Describe the risk factors in the plan."
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    budget_tokens: int = 8000
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])
    allow_web_search_for_spec: str = "auto"
    spec_max_search_queries: int = 2


@dataclass
class ModelConfig:
    backend: str = "openai"
    name: str | None = None
    planner: str | None = None
    worker: str | None = None
    judge: str | None = None
    aggregator: str | None = None
    api_key: str | None = None


@dataclass
class MemoryConfig:
    enabled: bool = False
    db_path: str = "memory/agent_cop.db"
    entity_id: str = "default"
    top_k: int = 3
    store_agent_outputs: bool = True
    store_judge_reports: bool = True
    auto_build: bool = False


@dataclass
class AppConfig:
    task: TaskConfig = field(default_factory=TaskConfig)
    method: str = "orchestrated"
    model: ModelConfig = field(default_factory=ModelConfig)
    log_dir: str = "logs"
    log: "LogConfig" = field(default_factory=lambda: LogConfig())
    repair: "RepairConfig" = field(default_factory=lambda: RepairConfig())
    memory: MemoryConfig = field(default_factory=MemoryConfig)


@dataclass
class LogConfig:
    enabled: bool = True
    level: str = "info"
    use_color: bool = True
    use_icons: bool = True
    preview_chars: int = 200
    show_prompts: bool = False
    show_outputs: bool = False


@dataclass
class RepairConfig:
    enabled: bool = True
    max_rounds: int = 1
    template_mode: str = "auto"  # off | auto | force


@dataclass
class EvalConfig:
    tasks: str = "data/tasks.jsonl"
    method: str = "orchestrated"
    model: ModelConfig = field(default_factory=ModelConfig)
    log_dir: str = "logs"
    log: "LogConfig" = field(default_factory=lambda: LogConfig())
    repair: "RepairConfig" = field(default_factory=lambda: RepairConfig())
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    max_samples: int | None = None
