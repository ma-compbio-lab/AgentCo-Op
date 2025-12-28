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
class AppConfig:
    task: TaskConfig = field(default_factory=TaskConfig)
    method: str = "orchestrated"
    model: ModelConfig = field(default_factory=ModelConfig)
    log_dir: str = "logs"
    log: "LogConfig" = field(default_factory=lambda: LogConfig())


@dataclass
class LogConfig:
    enabled: bool = True
    level: str = "info"
    use_color: bool = True
    use_icons: bool = True


@dataclass
class EvalConfig:
    tasks: str = "data/tasks.jsonl"
    method: str = "orchestrated"
    model: ModelConfig = field(default_factory=ModelConfig)
    log_dir: str = "logs"
    log: "LogConfig" = field(default_factory=lambda: LogConfig())
    max_samples: int | None = None
