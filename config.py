from __future__ import annotations

from dataclasses import dataclass, field

from core.local_exec import ExecConfig


@dataclass
class TaskConfig:
    goal: str = "Describe the risk factors in the plan."
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    budget_tokens: int = 8000
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])
    task_type: str = "auto"  # auto | coding | research | analysis | general
    prompt_verbosity: str = "normal"  # minimal | normal | verbose
    allow_web_search_for_spec: str = "auto"
    spec_max_search_queries: int = 2
    allow_tool_search: str = "auto"
    tool_max_candidates: int = 5
    tool_max_search_queries: int = 3

    def __post_init__(self) -> None:
        # Normalize YAML booleans like "on"/"off" into the expected string values.
        self.allow_web_search_for_spec = _normalize_toggle(self.allow_web_search_for_spec)
        self.allow_tool_search = _normalize_toggle(self.allow_tool_search)


def _normalize_toggle(value: str | bool | None) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return "auto"
    normalized = str(value).strip().lower()
    if normalized in {"on", "off", "auto"}:
        return normalized
    if normalized in {"true", "yes", "1"}:
        return "on"
    if normalized in {"false", "no", "0"}:
        return "off"
    return normalized


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
class PlanningConfig:
    enabled: bool = False
    db_path: str = "memory/planning.db"
    findings_db_path: str | None = None
    progress_db_path: str | None = None
    include_in_prompts: bool = True
    store_to_memory: bool = True
    max_items: int = 5


@dataclass
class ToolConfig:
    enabled: bool = False
    use_docker: bool = True
    base_image: str = "python:3.10-slim"
    build_allow_net: bool = True
    run_allow_net: bool = False
    default_timeout_s: int = 300
    default_cpus: float | None = None
    default_memory_mb: int | None = None
    default_pids: int | None = None
    run_dir: str = "logs/tool_runs"
    docker_repair_max_rounds: int = 2
    repo2run_enabled: bool = False
    repo2run_path: str = "third_party/Repo2Run"
    repo2run_python: str = "python"
    repo2run_llm: str | None = None
    repo2run_prefer_existing: bool = True
    repo2run_work_dir: str = "logs/repo2run"


@dataclass
class MCPConfig:
    enabled: bool = False
    approval_mode: str = "auto"  # auto | required | off
    cache_tools_list: bool = True
    enforce_tool_filter: bool = False
    servers: dict[str, dict] = field(default_factory=dict)
    prompts: dict[str, dict] = field(default_factory=dict)


@dataclass
class ChatConfig:
    enabled: bool = True
    db_path: str = "data/chat.db"
    history_turns: int = 8
    stream: bool = True


@dataclass
class AdaptiveConfig:
    enabled: bool = True
    router_model: str | None = None
    max_turns: int = 4
    confidence_threshold: float = 0.6
    fallback_to_heuristic: bool = True
    memory_top_k: int = 4
    force_mode: str = "auto"  # auto | single_agent | multi_agent
    enforce_self_contained_guardrails: bool = True


@dataclass
class AppConfig:
    task: TaskConfig = field(default_factory=TaskConfig)
    method: str = "orchestrated"
    model: ModelConfig = field(default_factory=ModelConfig)
    log_dir: str = "logs"
    log: "LogConfig" = field(default_factory=lambda: LogConfig())
    repair: "RepairConfig" = field(default_factory=lambda: RepairConfig())
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    tool: ToolConfig = field(default_factory=ToolConfig)
    exec: ExecConfig = field(default_factory=ExecConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    adaptive: AdaptiveConfig = field(default_factory=AdaptiveConfig)


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
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    tool: ToolConfig = field(default_factory=ToolConfig)
    exec: ExecConfig = field(default_factory=ExecConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    adaptive: AdaptiveConfig = field(default_factory=AdaptiveConfig)
    max_samples: int | None = None
