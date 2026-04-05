from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dynaforge.ir.schema import BudgetSpec, ModelSpec, NodeKind, SkillRef, TaskSpec, WorkflowBlueprint
from dynaforge.workflows.design import BlueprintCompilerConfig, TaskProfile, WorkflowPattern
from dynaforge.workflows.patterns import PatternBuildContext, build_pattern_blueprint
from dynaforge.workflows.synthesis import ComponentLibrary, ComponentSearcher
from dynaforge.workflows.synthesis.assembler import SynthesisAssembler, SynthesisError
from dynaforge.workflows.synthesis.validator import BlueprintValidator, BlueprintValidationError

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class GraphCandidate:
    pattern_id: str
    score: float
    reasons: List[str]
    source: str = "library"


class WorkflowCompiler:
    _component_library: ComponentLibrary | None = None

    @classmethod
    def _get_library(cls) -> ComponentLibrary:
        if cls._component_library is None:
            cls._component_library = ComponentLibrary()
        return cls._component_library

    def __init__(self, config: BlueprintCompilerConfig):
        self.config = config

    def compile(
        self,
        *,
        task: TaskSpec,
        budget: BudgetSpec,
        meta: Mapping[str, Any],
        model: ModelSpec,
        review_model: Optional[ModelSpec],
        resolved_config: Mapping[str, Any],
    ) -> tuple[WorkflowBlueprint, JsonDict]:
        review_model = review_model or model
        candidates = self.search_candidates(self.config.task_profile)
        selected_pattern, selection_mode = self._select_pattern(candidates)

        if selected_pattern == WorkflowPattern.synthesized_hybrid.value and self.config.synthesis.enabled:
            component_results = getattr(self, "_last_component_results", [])
            if component_results:
                try:
                    from dynaforge.runtime.llm import OpenAICompatibleLLMClient
                    llm_client = OpenAICompatibleLLMClient()
                    assembler = SynthesisAssembler(llm_client, review_model)
                    validator = BlueprintValidator()
                    context = PatternBuildContext(
                        task=task,
                        budget=budget,
                        meta=dict(meta),
                        model=model,
                        review_model=review_model,
                        design=self.config,
                        resolved_config=resolved_config,
                    )
                    blueprint = assembler.assemble(context, component_results)
                    validator.validate(blueprint)
                    self._attach_default_skills(blueprint)
                    compile_trace = {
                        "enabled": True,
                        "mode": self.config.mode,
                        "selection_mode": "composition-search",
                        "task_profile": self.config.task_profile.model_dump(),
                        "selected_pattern": selected_pattern,
                        "candidate_graphs": [
                            {"pattern_id": c.pattern_id, "score": round(c.score, 4), "reasons": c.reasons}
                            for c in candidates
                        ],
                        "top_components": [
                            {"component_id": cs.component_id, "role": cs.role, "score": round(score, 4)}
                            for cs, score in component_results[:5]
                        ],
                    }
                    blueprint.meta.update({"compile_trace": compile_trace})
                    return blueprint, compile_trace
                except (SynthesisError, BlueprintValidationError) as exc:
                    logger.warning("Synthesis assembly failed, falling back to pattern: %s", exc)

        context = PatternBuildContext(
            task=task,
            budget=budget,
            meta=dict(meta),
            model=model,
            review_model=review_model,
            design=self.config,
            resolved_config=resolved_config,
        )
        blueprint = build_pattern_blueprint(selected_pattern, context)
        self._attach_default_skills(blueprint)
        compile_trace = {
            "enabled": True,
            "mode": self.config.mode,
            "selection_mode": selection_mode,
            "task_profile": self.config.task_profile.model_dump(),
            "candidate_graphs": [
                {
                    "pattern_id": candidate.pattern_id,
                    "score": round(candidate.score, 4),
                    "reasons": list(candidate.reasons),
                    "source": candidate.source,
                }
                for candidate in candidates
            ],
            "selected_pattern": selected_pattern,
        }
        blueprint.meta.update({"compile_trace": compile_trace})
        return blueprint, compile_trace

    @staticmethod
    def _attach_default_skills(blueprint: WorkflowBlueprint) -> None:
        for node in blueprint.all_nodes():
            if node.kind not in {NodeKind.agent, NodeKind.evaluator, NodeKind.router}:
                continue
            if node.skills:
                continue
            node.skills.append(
                SkillRef(
                    name="structured-json-discipline",
                    optional=True,
                )
            )

    def search_candidates(self, profile: TaskProfile) -> List[GraphCandidate]:
        preferred = (self.config.preferred_pattern or "").strip()
        candidates: list[GraphCandidate] = []
        for pattern_id in [
            WorkflowPattern.direct_answer.value,
            WorkflowPattern.reason_execute_select.value,
            WorkflowPattern.code_generate_test_repair.value,
            WorkflowPattern.repo_transfer_validate.value,
            WorkflowPattern.closed_loop_design_validate.value,
            WorkflowPattern.specialist_assembly.value,
        ]:
            score, reasons = self._score_pattern(pattern_id, profile)
            if preferred and preferred == pattern_id:
                score += 0.2
                reasons.append("preferred-pattern boost")
            candidates.append(GraphCandidate(pattern_id=pattern_id, score=min(score, 1.0), reasons=reasons))
        candidates.sort(key=lambda item: (item.score, item.pattern_id), reverse=True)
        if self.config.synthesis.enabled:
            library = self._get_library()
            searcher = ComponentSearcher(
                library, top_k=self.config.synthesis.component_search_top_k
            )
            component_results = searcher.search(self.config.task_profile)
            if component_results:
                best_component_score = component_results[0][1]
                max_possible = max(r[1] for r in component_results) if component_results else 1.0
                normalized = min(best_component_score / max(max_possible, 1.0), 1.0)
                synth_score = self._synthesis_score(
                    max(normalized, candidates[0].score if candidates else 0.0)
                )
                candidates.append(
                    GraphCandidate(
                        pattern_id=WorkflowPattern.synthesized_hybrid.value,
                        score=synth_score,
                        reasons=[
                            f"composition-search: top component {component_results[0][0].role!r} "
                            f"score={component_results[0][1]:.3f}"
                        ],
                        source="synthesized",
                    )
                )
                candidates.sort(
                    key=lambda item: (item.score, item.source == "synthesized", item.pattern_id),
                    reverse=True,
                )
            self._last_component_results = component_results
        return candidates[: self.config.synthesis.max_candidates]

    def _select_pattern(self, candidates: List[GraphCandidate]) -> Tuple[str, str]:
        if not candidates:
            return WorkflowPattern.synthesized_hybrid.value, "empty-candidate-fallback"

        library_candidates = [candidate for candidate in candidates if candidate.source != "synthesized"]
        synthesized_candidate = next(
            (candidate for candidate in candidates if candidate.pattern_id == WorkflowPattern.synthesized_hybrid.value),
            None,
        )
        best_library = library_candidates[0] if library_candidates else None

        if self.config.mode == "synthesize_only":
            return WorkflowPattern.synthesized_hybrid.value, "forced-synthesis"
        if self.config.mode == "pattern_library":
            if best_library is not None:
                return best_library.pattern_id, "pattern-library"
            return WorkflowPattern.synthesized_hybrid.value, "pattern-library-empty"
        if self.config.search.prefer_synthesis:
            return WorkflowPattern.synthesized_hybrid.value, "prefer-synthesis"
        if best_library is not None and best_library.score >= self.config.search.min_pattern_score:
            return best_library.pattern_id, "pattern-search"
        if (
            self.config.search.fallback_to_synthesis
            and self.config.synthesis.enabled
            and synthesized_candidate is not None
        ):
            return WorkflowPattern.synthesized_hybrid.value, "score-fallback-synthesis"
        top = candidates[0]
        return top.pattern_id, "pattern-search-low-score"

    @staticmethod
    def _synthesis_score(best_pattern_score: float) -> float:
        return max(0.65, min(0.95, best_pattern_score + 0.05))

    @staticmethod
    def _score_pattern(pattern_id: str, profile: TaskProfile) -> tuple[float, List[str]]:
        score = 0.0
        reasons: list[str] = []
        if pattern_id == WorkflowPattern.direct_answer.value:
            if profile.answer_mode in {"choice_label", "freeform"}:
                score += 0.45
                reasons.append("direct-answer output mode")
            if not any(
                [
                    profile.requires_tool_execution,
                    profile.requires_test_execution,
                    profile.requires_repo_search,
                    profile.requires_closed_loop,
                    profile.requires_specialists,
                ]
            ):
                score += 0.35
                reasons.append("low orchestration requirement")
            if profile.domain in {"medical_qa", "knowledge_qa"}:
                score += 0.15
                reasons.append("qa-domain fit")
        elif pattern_id == WorkflowPattern.reason_execute_select.value:
            if profile.answer_mode == "exact_answer":
                score += 0.4
                reasons.append("exact-answer mode")
            if profile.requires_tool_execution:
                score += 0.3
                reasons.append("needs execution-backed verification")
            if profile.domain in {"math", "symbolic_reasoning"}:
                score += 0.2
                reasons.append("math/symbolic fit")
        elif pattern_id == WorkflowPattern.code_generate_test_repair.value:
            if profile.answer_mode == "code":
                score += 0.45
                reasons.append("code output mode")
            if profile.requires_test_execution:
                score += 0.35
                reasons.append("test execution required")
            if profile.domain in {"coding", "program_synthesis"}:
                score += 0.15
                reasons.append("coding-domain fit")
        elif pattern_id == WorkflowPattern.repo_transfer_validate.value:
            if profile.requires_repo_search:
                score += 0.35
                reasons.append("repo search required")
            if profile.requires_web_research:
                score += 0.15
                reasons.append("external evidence required")
            if profile.requires_artifact_validation:
                score += 0.2
                reasons.append("artifact validation required")
            if profile.domain in {"scientific_transfer", "repo_transfer"}:
                score += 0.2
                reasons.append("repo-transfer fit")
        elif pattern_id == WorkflowPattern.closed_loop_design_validate.value:
            if profile.requires_closed_loop:
                score += 0.45
                reasons.append("closed-loop optimization required")
            if profile.requires_artifact_validation:
                score += 0.2
                reasons.append("validation artifacts required")
            if profile.domain in {"panel_design", "design_optimization"}:
                score += 0.2
                reasons.append("design-optimization fit")
        elif pattern_id == WorkflowPattern.specialist_assembly.value:
            if profile.requires_specialists:
                score += 0.45
                reasons.append("specialist collaboration required")
            if profile.requires_repo_search:
                score += 0.15
                reasons.append("specialists likely external repos")
            if profile.requires_artifact_validation:
                score += 0.15
                reasons.append("integration should be validated")
            if profile.domain in {"specialist_collaboration", "multi_agent_science"}:
                score += 0.15
                reasons.append("specialist-collaboration fit")
        if profile.complexity == "high":
            if pattern_id in {
                WorkflowPattern.repo_transfer_validate.value,
                WorkflowPattern.closed_loop_design_validate.value,
                WorkflowPattern.specialist_assembly.value,
            }:
                score += 0.1
                reasons.append("high-complexity boost")
        return min(score, 1.0), reasons


def extract_compiler_config(config: Mapping[str, Any]) -> Optional[BlueprintCompilerConfig]:
    direct = config.get("workflow_design")
    if isinstance(direct, Mapping):
        return BlueprintCompilerConfig.model_validate(direct)
    experiment = config.get("experiment")
    if isinstance(experiment, Mapping):
        nested = experiment.get("workflow_design")
        if isinstance(nested, Mapping):
            return BlueprintCompilerConfig.model_validate(nested)
    return None


def compile_workflow_from_config(config: Mapping[str, Any]) -> tuple[WorkflowBlueprint, JsonDict]:
    compiler_cfg = extract_compiler_config(config)
    if compiler_cfg is None or not compiler_cfg.enabled:
        raise ValueError("compile_workflow_from_config requires enabled workflow_design config")

    blueprint_payload = config.get("blueprint", {})
    if not isinstance(blueprint_payload, Mapping):
        blueprint_payload = {}
    task_payload = blueprint_payload.get("task", {}) if isinstance(blueprint_payload, Mapping) else {}
    budget_payload = blueprint_payload.get("budget", {}) if isinstance(blueprint_payload, Mapping) else {}
    meta_payload = blueprint_payload.get("meta", {}) if isinstance(blueprint_payload, Mapping) else {}
    task = TaskSpec.model_validate(task_payload or {"task_id": "task", "title": "Task", "description": ""})
    budget = BudgetSpec.model_validate(budget_payload or {})
    model = ModelSpec.model_validate(config.get("model", {}))
    review_model_payload = config.get("review_model", config.get("model", {}))
    review_model = ModelSpec.model_validate(review_model_payload)
    compiler = WorkflowCompiler(compiler_cfg)
    return compiler.compile(
        task=task,
        budget=budget,
        meta=dict(meta_payload) if isinstance(meta_payload, Mapping) else {},
        model=model,
        review_model=review_model,
        resolved_config=config,
    )
