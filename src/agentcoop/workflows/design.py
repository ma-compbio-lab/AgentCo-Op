from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

JsonDict = Dict[str, Any]


class WorkflowPattern(str, Enum):
    direct_answer = "direct_answer"
    reason_execute_select = "reason_execute_select"
    code_generate_test_repair = "code_generate_test_repair"
    repo_transfer_validate = "repo_transfer_validate"
    closed_loop_design_validate = "closed_loop_design_validate"
    specialist_assembly = "specialist_assembly"
    synthesized_hybrid = "synthesized_hybrid"


class TaskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = "generic"
    answer_mode: Literal["choice_label", "exact_answer", "code", "artifact_bundle", "freeform"] = "freeform"
    complexity: Literal["low", "medium", "high"] = "medium"
    requires_tool_execution: bool = False
    requires_test_execution: bool = False
    requires_repo_search: bool = False
    requires_web_research: bool = False
    requires_closed_loop: bool = False
    requires_specialists: bool = False
    requires_artifact_validation: bool = False
    preferred_review_style: Literal["none", "gate", "always"] = "gate"
    review_threshold: float = Field(default=0.72, ge=0, le=1)
    high_risk_hint_field: str = ""
    high_risk_hint_values: List[str] = Field(default_factory=list)
    expected_artifacts: List[str] = Field(default_factory=list)
    notes: str = ""


class PatternSearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min_pattern_score: float = Field(default=0.6, ge=0, le=1)
    prefer_synthesis: bool = False
    fallback_to_synthesis: bool = True


class GraphSynthesisSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    allow_from_scratch: bool = True
    llm_planner: bool = False
    max_candidates: int = Field(default=4, ge=1, le=8)
    component_search_top_k: int = Field(default=10, ge=1, le=50)


class RuntimeExpansionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_expansions: int = Field(default=2, ge=0, le=8)
    expand_on_low_confidence: bool = True
    expand_on_eval_failure: bool = True
    expand_on_tool_failure: bool = True
    expand_on_output_signal: bool = True


class MonitoringSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    record_compile_trace: bool = True
    record_runtime_events: bool = True
    record_graph_candidates: bool = True
    record_tool_search: bool = True
    record_web_search: bool = True


class SkillDrivenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    min_meta_skill_score: float = Field(default=0.4, ge=0, le=1)
    max_skills_per_agent: int = Field(default=3, ge=1, le=10)
    deduplicate_skills: bool = True
    skill_search_paths: List[str] = Field(default_factory=list)


class BlueprintCompilerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: Literal["static", "pattern_library", "pattern_then_synthesize", "synthesize_only"] = "pattern_then_synthesize"
    preferred_pattern: Optional[str] = None
    task_profile: TaskProfile = Field(default_factory=TaskProfile)
    search: PatternSearchSpec = Field(default_factory=PatternSearchSpec)
    synthesis: GraphSynthesisSpec = Field(default_factory=GraphSynthesisSpec)
    runtime_expansion: RuntimeExpansionSpec = Field(default_factory=RuntimeExpansionSpec)
    monitoring: MonitoringSpec = Field(default_factory=MonitoringSpec)
    skill_driven: SkillDrivenSpec = Field(default_factory=SkillDrivenSpec)
    pattern_overrides: JsonDict = Field(default_factory=dict)

    def pattern_override(self, pattern_id: str) -> JsonDict:
        specific = self.pattern_overrides.get(pattern_id)
        if isinstance(specific, dict):
            return dict(specific)
        return dict(self.pattern_overrides)
