from __future__ import annotations

import ast
import gzip
import json
import math
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from omegaconf import DictConfig, OmegaConf

from agentcoop.benchmarks_lib import (
    BenchmarkSetupError,
    probe_humaneval_call,
    grade_humaneval_prediction,
    grade_math_prediction,
    _math_equal,
    prepare_case_study_assets,
    prepare_humaneval_dataset,
    prepare_math_dataset,
    run_humaneval_public_tests,
)
from agentcoop.case_studies.legacy_registry import LEGACY_SQUIDPY_CASE_IDS, legacy_case_default_run_name
from agentcoop.config import build_blueprint_from_config, build_executor_from_config, resolve_hydra_config
from agentcoop.ir import (
    BudgetSpec,
    EdgeSpec,
    EvalSpec,
    HardCheck,
    IOContract,
    MCPServerRef,
    ModelSpec,
    NodeKind,
    NodeSpec,
    Repo2RunSpec,
    SandboxBackend,
    SandboxSpec,
    SoftJudge,
    TaskSpec,
    ToolRef,
    WorkflowBlueprint,
)
from agentcoop.runtime import ExecutionCost, ExecutionReport, NodeExecutionResult
from agentcoop.ir.schema import AtomicCondition, ConditionOp, FailureType, GateSpec, SubgraphSpec, TriggerExpr
from agentcoop.runtime.llm import LLMClientError, OpenAICompatibleLLMClient
from agentcoop.secrets import load_local_secrets


@dataclass
class RunRecorder:
    experiment_name: str
    base_dir: str | Path = "runs"
    run_id: str | None = None
    root_dir: str | Path | None = None

    def __post_init__(self) -> None:
        if self.root_dir is not None:
            self.root = Path(self.root_dir).resolve()
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            run_suffix = self.run_id or uuid.uuid4().hex[:8]
            slug = _slugify(self.experiment_name)
            self.root = Path(self.base_dir).resolve() / slug / f"{stamp}_{run_suffix}"
        self.config_dir = self.root / "config"
        self.logs_dir = self.root / "logs"
        self.traces_dir = self.root / "traces"
        self.artifacts_dir = self.root / "artifacts"
        self.eval_dir = self.root / "eval"
        self.summaries_dir = self.root / "summaries"
        for path in (
            self.config_dir,
            self.logs_dir,
            self.traces_dir,
            self.artifacts_dir,
            self.eval_dir,
            self.summaries_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative_path: str | Path, payload: Any) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        if str(path).endswith(".gz"):
            with gzip.open(path, "wb") as f:
                f.write(data)
        else:
            path.write_bytes(data)
        return path

    @staticmethod
    def read_json(path: Path) -> Any:
        if str(path).endswith(".gz"):
            with gzip.open(path, "rb") as f:
                return json.loads(f.read().decode("utf-8"))
        return json.loads(path.read_text(encoding="utf-8"))

    def write_text(self, relative_path: str | Path, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


def run_stage0_validation(
    math_config: DictConfig | Mapping[str, Any],
    humaneval_config: DictConfig | Mapping[str, Any],
    *,
    base_dir: str | Path = "runs",
) -> dict[str, Any]:
    load_local_secrets()
    recorder = RunRecorder("stage0_validation", base_dir=base_dir)
    math_resolved = resolve_hydra_config(math_config)
    humaneval_resolved = resolve_hydra_config(humaneval_config)

    recorder.write_text("config/math_config.yaml", _config_to_yaml(math_config))
    recorder.write_text("config/humaneval_config.yaml", _config_to_yaml(humaneval_config))
    metadata = collect_run_metadata(math_resolved)
    recorder.write_json("summaries/run_metadata.json", metadata)

    stage0_summary: dict[str, Any] = {
        "metadata": metadata,
        "capabilities": detect_runtime_capabilities(),
        "api_probe": probe_model_access(math_resolved.get("model", {})),
    }

    try:
        stage0_summary["math_manifest"] = prepare_math_dataset(math_resolved)
    except Exception as exc:
        stage0_summary["math_error"] = str(exc)

    try:
        stage0_summary["humaneval_manifest"] = prepare_humaneval_dataset(humaneval_resolved)
    except Exception as exc:
        stage0_summary["humaneval_error"] = str(exc)

    stage0_summary["success"] = bool(
        stage0_summary["api_probe"].get("ok")
        and "math_error" not in stage0_summary
        and "humaneval_error" not in stage0_summary
    )
    recorder.write_json("summaries/stage0_summary.json", stage0_summary)
    recorder.write_text("summaries/stage0_summary.md", _render_stage0_markdown(stage0_summary))
    return {**stage0_summary, "run_dir": str(recorder.root)}


def run_math_experiment(
    config: DictConfig | Mapping[str, Any],
    *,
    subset: str = "validation",
    limit: Optional[int] = None,
    task_ids: Optional[Sequence[str]] = None,
    num_shards: int = 1,
    shard_index: int = 0,
    base_dir: str | Path = "runs",
    run_name: Optional[str] = None,
    resume_run_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    load_local_secrets()
    resolved = resolve_hydra_config(config)
    if num_shards < 1:
        raise BenchmarkSetupError("num_shards must be >= 1")
    if shard_index < 0 or shard_index >= num_shards:
        raise BenchmarkSetupError("shard_index must satisfy 0 <= shard_index < num_shards")
    if run_name is not None:
        run_label = run_name
    elif num_shards > 1:
        run_label = f"math_{subset}_shard{shard_index:02d}of{num_shards:02d}"
    else:
        run_label = f"math_{subset}"
    recorder = RunRecorder(run_label, base_dir=base_dir, root_dir=resume_run_dir)
    recorder.write_text("config/resolved_config.yaml", _config_to_yaml(config))
    run_metadata = collect_run_metadata(resolved)
    if resume_run_dir is not None:
        run_metadata["resume_run_dir"] = str(Path(resume_run_dir).resolve())
        run_metadata["resumed"] = True

    manifest = prepare_math_dataset(resolved)
    records = _read_jsonl(Path(manifest["records_path"]))
    indices = _resolve_generic_indices(records, manifest, subset=subset, limit=limit, task_ids=task_ids)
    indices = _apply_shard(indices, num_shards=num_shards, shard_index=shard_index)
    recorder.write_json("eval/selected_indices.json", indices)

    base_blueprint = build_blueprint_from_config(resolved)
    executor = build_executor_from_config(resolved, artifact_root=recorder.artifacts_dir)
    preferred_nodes = _preferred_answer_nodes(resolved, default=("reviser", "reviewer", "solver"))
    task_results = _load_existing_generic_task_results(
        run_root=recorder.root,
        records=records,
        selected_indices=indices,
        preferred_nodes=preferred_nodes,
        result_builder=lambda *, sample, sample_index, order, report, report_path: _build_math_task_result(
            sample=sample,
            sample_index=sample_index,
            order=order,
            report=report,
            report_path=report_path,
            preferred_nodes=preferred_nodes,
        ),
    )
    task_results_by_id = {str(result.get("task_id", "")): dict(result) for result in task_results}
    recorder.write_json("summaries/run_metadata.json", run_metadata)

    for order, index in enumerate(indices, start=1):
        sample = records[index]
        sample_id = str(sample["id"])
        if sample_id in task_results_by_id:
            continue
        task_blueprint = build_math_task_blueprint(base_blueprint, sample)
        handlers = _build_math_task_handlers(resolved, task_blueprint, sample)
        try:
            _, report = _run_blueprint(resolved, task_blueprint, executor, handlers=handlers)
            report_path = recorder.write_json(f"traces/{sample_id}.report.json", report.model_dump())
            task_result = _build_math_task_result(
                sample=sample,
                sample_index=index,
                order=order,
                report=report,
                report_path=report_path,
                preferred_nodes=preferred_nodes,
            )
        except Exception as exc:
            task_result = _build_generic_exception_result(
                sample=sample,
                sample_index=index,
                order=order,
                error=exc,
                benchmark="math",
            )
        task_results_by_id[sample_id] = task_result
        recorder.write_json("eval/task_results.json", _sorted_task_results(task_results_by_id.values()))

    task_results = _sorted_task_results(task_results_by_id.values())
    summary = _summarize_generic_task_results(
        benchmark="math",
        metric_name="solve_rate",
        task_results=task_results,
        subset=subset,
        manifest=manifest,
        requested_task_ids=list(task_ids or []),
        num_shards=num_shards,
        shard_index=shard_index,
    )
    summary["run_dir"] = str(recorder.root)
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_generic_benchmark_analysis(summary, task_results))
    return summary


def run_humaneval_experiment(
    config: DictConfig | Mapping[str, Any],
    *,
    subset: str = "validation",
    limit: Optional[int] = None,
    task_ids: Optional[Sequence[str]] = None,
    num_shards: int = 1,
    shard_index: int = 0,
    base_dir: str | Path = "runs",
    run_name: Optional[str] = None,
    resume_run_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    load_local_secrets()
    resolved = resolve_hydra_config(config)
    if num_shards < 1:
        raise BenchmarkSetupError("num_shards must be >= 1")
    if shard_index < 0 or shard_index >= num_shards:
        raise BenchmarkSetupError("shard_index must satisfy 0 <= shard_index < num_shards")
    if run_name is not None:
        run_label = run_name
    elif num_shards > 1:
        run_label = f"humaneval_{subset}_shard{shard_index:02d}of{num_shards:02d}"
    else:
        run_label = f"humaneval_{subset}"
    recorder = RunRecorder(run_label, base_dir=base_dir, root_dir=resume_run_dir)
    recorder.write_text("config/resolved_config.yaml", _config_to_yaml(config))
    run_metadata = collect_run_metadata(resolved)
    if resume_run_dir is not None:
        run_metadata["resume_run_dir"] = str(Path(resume_run_dir).resolve())
        run_metadata["resumed"] = True

    manifest = prepare_humaneval_dataset(resolved)
    records = _read_jsonl(Path(manifest["records_path"]))
    indices = _resolve_generic_indices(records, manifest, subset=subset, limit=limit, task_ids=task_ids)
    indices = _apply_shard(indices, num_shards=num_shards, shard_index=shard_index)
    recorder.write_json("eval/selected_indices.json", indices)

    base_blueprint = build_blueprint_from_config(resolved)
    executor = build_executor_from_config(resolved, artifact_root=recorder.artifacts_dir)
    preferred_nodes = _preferred_answer_nodes(resolved, default=("reviser", "reviewer", "coder"))
    task_results = _load_existing_generic_task_results(
        run_root=recorder.root,
        records=records,
        selected_indices=indices,
        preferred_nodes=preferred_nodes,
        result_builder=lambda *, sample, sample_index, order, report, report_path: _build_humaneval_task_result(
            sample=sample,
            sample_index=sample_index,
            order=order,
            report=report,
            report_path=report_path,
            preferred_nodes=preferred_nodes,
        ),
    )
    task_results_by_id = {str(result.get("task_id", "")): dict(result) for result in task_results}
    recorder.write_json("summaries/run_metadata.json", run_metadata)

    for order, index in enumerate(indices, start=1):
        sample = records[index]
        sample_id = str(sample["id"])
        if sample_id in task_results_by_id:
            continue
        task_blueprint = build_humaneval_task_blueprint(base_blueprint, sample)
        handlers = _build_humaneval_task_handlers(resolved, task_blueprint, sample)
        try:
            _, report = _run_blueprint(resolved, task_blueprint, executor, handlers=handlers)
            report_path = recorder.write_json(f"traces/{sample_id}.report.json", report.model_dump())
            task_result = _build_humaneval_task_result(
                sample=sample,
                sample_index=index,
                order=order,
                report=report,
                report_path=report_path,
                preferred_nodes=preferred_nodes,
            )
        except Exception as exc:
            task_result = _build_generic_exception_result(
                sample=sample,
                sample_index=index,
                order=order,
                error=exc,
                benchmark="humaneval",
            )
        task_results_by_id[sample_id] = task_result
        recorder.write_json("eval/task_results.json", _sorted_task_results(task_results_by_id.values()))

    task_results = _sorted_task_results(task_results_by_id.values())
    summary = _summarize_generic_task_results(
        benchmark="humaneval",
        metric_name="pass_at_1",
        task_results=task_results,
        subset=subset,
        manifest=manifest,
        requested_task_ids=list(task_ids or []),
        num_shards=num_shards,
        shard_index=shard_index,
    )
    summary["run_dir"] = str(recorder.root)
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_generic_benchmark_analysis(summary, task_results))
    return summary


def run_case_study_experiment(
    config: DictConfig | Mapping[str, Any],
    *,
    base_dir: str | Path = "runs",
    run_name: Optional[str] = None,
) -> dict[str, Any]:
    load_local_secrets()
    resolved = resolve_hydra_config(config)
    benchmark_settings = dict(resolved.get("benchmark", {}))
    if benchmark_settings.get("kind") != "case_study":
        raise BenchmarkSetupError("Case-study runner requires benchmark.kind=case_study")
    runner_mode = str(benchmark_settings.get("runner_mode", "")).strip().lower()
    case_id = str(benchmark_settings.get("case_id", "")).strip()
    if not case_id and runner_mode not in {"compiled_generic", "generic_compiled"}:
        raise BenchmarkSetupError("Case-study runner requires benchmark.case_id")

    if runner_mode in {"compiled_generic", "generic_compiled"}:
        return _run_generic_case_study_experiment(
            resolved,
            benchmark_settings=benchmark_settings,
            base_dir=base_dir,
            run_name=run_name,
            case_id=case_id or "generic_case_study",
        )

    return _run_legacy_case_study_experiment(
        config,
        resolved=resolved,
        benchmark_settings=benchmark_settings,
        base_dir=base_dir,
        run_name=run_name,
        case_id=case_id,
    )


def _run_legacy_case_study_experiment(
    config: DictConfig | Mapping[str, Any],
    *,
    resolved: Mapping[str, Any],
    benchmark_settings: Mapping[str, Any],
    base_dir: str | Path,
    run_name: Optional[str],
    case_id: str,
) -> dict[str, Any]:
    default_run_name = legacy_case_default_run_name(case_id)

    recorder = RunRecorder(run_name or default_run_name, base_dir=base_dir)
    recorder.write_text("config/resolved_config.yaml", _config_to_yaml(config))
    run_metadata = collect_run_metadata(resolved)
    recorder.write_json("summaries/run_metadata.json", run_metadata)

    case_assets = prepare_case_study_assets(resolved)
    executor = build_executor_from_config(resolved, artifact_root=recorder.artifacts_dir)

    if case_id == "scanpy_pbmc3k_umap":
        sandbox = _build_scanpy_case_sandbox(benchmark_settings, case_assets)
        reference_assets = _ensure_scanpy_case_reference_assets(
            case_assets=case_assets,
            benchmark_settings=benchmark_settings,
            sandbox=sandbox,
            sandbox_runner=executor.sandbox_runner,
        )
        blueprint = _build_scanpy_case_blueprint(
            resolved,
            benchmark_settings=benchmark_settings,
            case_assets=case_assets,
            reference_assets=reference_assets,
            recorder=recorder,
            sandbox=sandbox,
        )
        handlers = {
            "planner": _scanpy_planner_handler,
            "scanpy_tool": _scanpy_tool_handler,
            "scanpy_tool_refined": _scanpy_tool_handler,
            "case_refiner": _case_refiner_handler,
        }
        summarize = _summarize_scanpy_case_study
        render_analysis = render_scanpy_case_study_analysis
        render_narrative = render_scanpy_case_study_narrative
    elif case_id in {
        "scanpy_paul15_trajectory",
        "scanpy_paul15_paper_figure",
        "scanpy_moignard15_method_transfer",
        "scanpy_krumsiek11_method_transfer",
    }:
        sandbox = _build_scanpy_case_sandbox(benchmark_settings, case_assets)
        reference_assets = _ensure_scanpy_paul15_reference_assets(
            case_assets=case_assets,
            benchmark_settings=benchmark_settings,
            sandbox=sandbox,
            sandbox_runner=executor.sandbox_runner,
        )
        blueprint = _build_scanpy_paul15_blueprint(
            resolved,
            benchmark_settings=benchmark_settings,
            case_assets=case_assets,
            reference_assets=reference_assets,
            recorder=recorder,
            sandbox=sandbox,
        )
        handlers = {
            "planner": _scanpy_paul15_planner_handler,
            "scanpy_trajectory_tool": _scanpy_tool_handler,
            "scanpy_trajectory_tool_refined": _scanpy_tool_handler,
            "bio_reviewer": _scanpy_paul15_review_handler,
            "bio_reviewer_refined": _scanpy_paul15_review_handler,
            "case_refiner": _case_refiner_handler,
        }
        summarize = _summarize_scanpy_paul15_case_study
        render_analysis = render_scanpy_paul15_case_study_analysis
        render_narrative = render_scanpy_paul15_case_study_narrative
    elif case_id in {
        "scanpy_paul15_specialized_collaboration",
        "scanpy_moignard15_specialized_collaboration",
        "scanpy_krumsiek11_specialized_collaboration",
    }:
        paper_spec_sandbox = _build_scanpy_case_sandbox({"sandbox": benchmark_settings.get("paper_spec_sandbox", {})}, case_assets)
        planner_sandbox = _build_scanpy_case_sandbox({"sandbox": benchmark_settings.get("planner_sandbox", {})}, case_assets)
        trajectory_sandbox = _build_scanpy_case_sandbox({"sandbox": benchmark_settings.get("trajectory_sandbox", {})}, case_assets)
        critic_sandbox = _build_scanpy_case_sandbox({"sandbox": benchmark_settings.get("critic_sandbox", {})}, case_assets)
        reference_assets = _ensure_scanpy_paul15_reference_assets(
            case_assets=case_assets,
            benchmark_settings=benchmark_settings,
            sandbox=trajectory_sandbox,
            sandbox_runner=executor.sandbox_runner,
        )
        blueprint = _build_scanpy_paul15_specialized_collaboration_blueprint(
            resolved,
            benchmark_settings=benchmark_settings,
            case_assets=case_assets,
            reference_assets=reference_assets,
            recorder=recorder,
            paper_spec_sandbox=paper_spec_sandbox,
            planner_sandbox=planner_sandbox,
            trajectory_sandbox=trajectory_sandbox,
            critic_sandbox=critic_sandbox,
        )
        handlers = {
            "paper_spec": _paper_specialist_tool_handler,
            "planner": _paper_specialist_tool_handler,
            "scanpy_trajectory_tool": _scanpy_tool_handler,
            "scanpy_trajectory_tool_refined": _scanpy_tool_handler,
            "bio_reviewer": _paper_specialist_tool_handler,
            "bio_reviewer_refined": _paper_specialist_tool_handler,
            "case_refiner": _case_refiner_handler,
        }
        summarize = _summarize_scanpy_paul15_case_study
        render_analysis = render_scanpy_paul15_case_study_analysis
        render_narrative = render_scanpy_paul15_case_study_narrative
    elif case_id in {"visium_multi_agent_collaboration", "seqfish_multi_agent_collaboration"}:
        scanpy_sandbox = _build_scanpy_case_sandbox({"sandbox": benchmark_settings.get("scanpy_sandbox", {})}, case_assets)
        squidpy_sandbox = _build_squidpy_case_sandbox({"sandbox": benchmark_settings.get("squidpy_sandbox", {})}, case_assets)
        reference_assets = _ensure_visium_multi_agent_reference_assets(
            case_assets=case_assets,
            benchmark_settings=benchmark_settings,
            sandbox=squidpy_sandbox,
            sandbox_runner=executor.sandbox_runner,
        )
        blueprint = _build_visium_multi_agent_collaboration_blueprint(
            resolved,
            benchmark_settings=benchmark_settings,
            case_assets=case_assets,
            reference_assets=reference_assets,
            recorder=recorder,
            scanpy_sandbox=scanpy_sandbox,
            squidpy_sandbox=squidpy_sandbox,
        )
        handlers = {
            "planner": _visium_multi_agent_planner_handler,
            "scanpy_cluster_tool": _scanpy_visium_cluster_tool_handler,
            "squidpy_interaction_tool": _visium_squidpy_interaction_tool_handler,
        }
        summarize = _summarize_visium_multi_agent_collaboration_case_study
        render_analysis = render_visium_multi_agent_collaboration_analysis
        render_narrative = render_visium_multi_agent_collaboration_narrative
    elif case_id in {"visium_multi_agent_monolith", "seqfish_multi_agent_monolith"}:
        squidpy_sandbox = _build_squidpy_case_sandbox({"sandbox": benchmark_settings.get("squidpy_sandbox", {})}, case_assets)
        reference_assets = _ensure_visium_multi_agent_reference_assets(
            case_assets=case_assets,
            benchmark_settings=benchmark_settings,
            sandbox=squidpy_sandbox,
            sandbox_runner=executor.sandbox_runner,
        )
        blueprint = _build_visium_multi_agent_monolith_blueprint(
            resolved,
            benchmark_settings=benchmark_settings,
            case_assets=case_assets,
            reference_assets=reference_assets,
            recorder=recorder,
            sandbox=squidpy_sandbox,
        )
        handlers = {
            "planner": _visium_multi_agent_planner_handler,
            "visium_monolith_tool": _visium_multi_agent_monolith_tool_handler,
        }
        summarize = _summarize_visium_multi_agent_monolith_case_study
        render_analysis = render_visium_multi_agent_monolith_analysis
        render_narrative = render_visium_multi_agent_monolith_narrative
    elif case_id in LEGACY_SQUIDPY_CASE_IDS:
        sandbox = _build_squidpy_case_sandbox(benchmark_settings, case_assets)
        reference_assets = _ensure_squidpy_case_reference_assets(
            case_assets=case_assets,
            benchmark_settings=benchmark_settings,
            sandbox=sandbox,
            sandbox_runner=executor.sandbox_runner,
        )
        blueprint = _build_squidpy_case_blueprint(
            resolved,
            benchmark_settings=benchmark_settings,
            case_assets=case_assets,
            reference_assets=reference_assets,
            recorder=recorder,
            sandbox=sandbox,
        )
        handlers = {
            "planner": _squidpy_planner_handler,
            "squidpy_tool": _squidpy_tool_handler,
            "squidpy_tool_refined": _squidpy_tool_handler,
            "case_refiner": _case_refiner_handler,
        }
        summarize = _summarize_squidpy_case_study
        render_analysis = render_squidpy_case_study_analysis
        render_narrative = render_squidpy_case_study_narrative
    else:
        raise BenchmarkSetupError(f"Unsupported case-study id: {case_id}")

    _, report = _run_blueprint(resolved, blueprint, executor, handlers=handlers)

    summary = summarize(
        report,
        case_assets=case_assets,
        reference_assets=reference_assets,
    )
    summary["run_dir"] = str(recorder.root)

    recorder.write_json("eval/execution_report.json", report.model_dump(mode="json"))
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_analysis(summary))
    recorder.write_text("summaries/case_narrative.md", render_narrative(summary))
    return summary


def _run_generic_case_study_experiment(
    resolved: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    base_dir: str | Path,
    run_name: Optional[str],
    case_id: str,
) -> dict[str, Any]:
    default_run_name = run_name or str(benchmark_settings.get("run_name", "")).strip() or case_id or "case_study"
    recorder = RunRecorder(default_run_name, base_dir=base_dir)
    recorder.write_text("config/resolved_config.yaml", _config_to_yaml(resolved))
    run_metadata = collect_run_metadata(resolved)
    recorder.write_json("summaries/run_metadata.json", run_metadata)

    case_assets = prepare_case_study_assets(resolved)
    executor = build_executor_from_config(resolved, artifact_root=recorder.artifacts_dir)
    blueprint = _build_generic_case_study_blueprint(
        resolved,
        benchmark_settings=benchmark_settings,
        case_assets=case_assets,
        recorder=recorder,
    )
    recorder.write_json("eval/compiled_blueprint.json", blueprint.model_dump(mode="json"))

    generic_handlers: dict[str, Any] = {}
    if str(blueprint.meta.get("workflow_pattern", "")).strip() == "specialist_assembly":
        generic_handlers["specialist_router"] = _generic_specialist_router_handler
        generic_handlers["specialist_repair"] = _generic_specialist_router_handler

    _, report = _run_blueprint(resolved, blueprint, executor, handlers=generic_handlers or None)
    summary = _summarize_generic_case_study(
        report,
        blueprint=blueprint,
        case_assets=case_assets,
    )
    summary["run_dir"] = str(recorder.root)
    recorder.write_json("eval/execution_report.json", report.model_dump(mode="json"))
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_generic_case_study_analysis(summary))
    recorder.write_text("summaries/case_narrative.md", render_generic_case_study_narrative(summary))
    return summary


def run_scanpy_pbmc3k_case_study(
    config: DictConfig | Mapping[str, Any],
    *,
    base_dir: str | Path = "runs",
    run_name: Optional[str] = None,
) -> dict[str, Any]:
    return run_case_study_experiment(config, base_dir=base_dir, run_name=run_name)


def aggregate_math_runs(
    run_dirs: Sequence[str | Path],
    *,
    base_dir: str | Path = "runs",
    run_name: str = "math_aggregate",
) -> dict[str, Any]:
    return _aggregate_generic_runs(
        run_dirs,
        benchmark="math",
        metric_name="solve_rate",
        base_dir=base_dir,
        run_name=run_name,
    )


def aggregate_humaneval_runs(
    run_dirs: Sequence[str | Path],
    *,
    base_dir: str | Path = "runs",
    run_name: str = "humaneval_aggregate",
) -> dict[str, Any]:
    return _aggregate_generic_runs(
        run_dirs,
        benchmark="humaneval",
        metric_name="pass_at_1",
        base_dir=base_dir,
        run_name=run_name,
    )


def aggregate_math_repeats(
    run_dirs: Sequence[str | Path],
    *,
    base_dir: str | Path = "runs",
    run_name: str = "math_repeat_aggregate",
) -> dict[str, Any]:
    return _aggregate_repeat_runs(
        run_dirs,
        benchmark="math",
        metric_name="solve_rate",
        base_dir=base_dir,
        run_name=run_name,
    )


def aggregate_humaneval_repeats(
    run_dirs: Sequence[str | Path],
    *,
    base_dir: str | Path = "runs",
    run_name: str = "humaneval_repeat_aggregate",
) -> dict[str, Any]:
    return _aggregate_repeat_runs(
        run_dirs,
        benchmark="humaneval",
        metric_name="pass_at_1",
        base_dir=base_dir,
        run_name=run_name,
    )


def _aggregate_generic_runs(
    run_dirs: Sequence[str | Path],
    *,
    benchmark: str,
    metric_name: str,
    base_dir: str | Path,
    run_name: str,
) -> dict[str, Any]:
    if not run_dirs:
        raise BenchmarkSetupError(f"aggregate_{benchmark}_runs requires at least one run directory")

    recorder = RunRecorder(run_name, base_dir=base_dir)
    all_results: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()

    for run_dir in [Path(path).resolve() for path in run_dirs]:
        task_results_path = run_dir / "eval" / "task_results.json"
        summary_path = run_dir / "summaries" / "summary.json"
        if not task_results_path.exists():
            raise BenchmarkSetupError(f"Missing {benchmark} task results: {task_results_path}")
        task_results = json.loads(task_results_path.read_text(encoding="utf-8"))
        for result in task_results:
            task_id = str(result.get("task_id", ""))
            if task_id in seen_task_ids:
                raise BenchmarkSetupError(f"Duplicate {benchmark} task_id in aggregate inputs: {task_id}")
            seen_task_ids.add(task_id)
            all_results.append(result)
        if summary_path.exists():
            source_summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))

    all_results.sort(key=lambda item: (int(item.get("sample_index", 0)), str(item.get("task_id", ""))))
    summary = _summarize_generic_task_results(
        benchmark=benchmark,
        metric_name=metric_name,
        task_results=all_results,
        subset="aggregate",
        manifest=source_summaries[0].get("manifest", {}) if source_summaries else {},
        requested_task_ids=[],
        num_shards=len(run_dirs),
        shard_index=-1,
    )
    summary["source_run_dirs"] = [str(Path(path).resolve()) for path in run_dirs]
    summary["run_dir"] = str(recorder.root)

    recorder.write_json("eval/task_results.json", all_results)
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_generic_benchmark_analysis(summary, all_results))
    return summary


def _aggregate_repeat_runs(
    run_dirs: Sequence[str | Path],
    *,
    benchmark: str,
    metric_name: str,
    base_dir: str | Path,
    run_name: str,
) -> dict[str, Any]:
    if not run_dirs:
        raise BenchmarkSetupError(f"aggregate_{benchmark}_repeats requires at least one run directory")

    recorder = RunRecorder(run_name, base_dir=base_dir)
    per_repeat: list[dict[str, Any]] = []
    source_repeat_dirs: list[str] = []
    subset_values: set[str] = set()
    model_values: set[str] = set()
    task_counts: set[int] = set()
    manifest_fingerprints: set[str] = set()
    manifest_payload: dict[str, Any] = {}

    for run_dir in [Path(path).resolve() for path in run_dirs]:
        summary_path = run_dir / "summaries" / "summary.json"
        if not summary_path.exists():
            raise BenchmarkSetupError(f"Missing repeat aggregate summary: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if str(summary.get("benchmark", "")) != benchmark:
            raise BenchmarkSetupError(f"Repeat aggregate benchmark mismatch at {summary_path}")
        source_run_dirs = [Path(path).resolve() for path in summary.get("source_run_dirs", [])]
        if not source_run_dirs:
            raise BenchmarkSetupError(f"Repeat aggregate missing source_run_dirs: {summary_path}")
        first_source_dir = source_run_dirs[0]
        source_summary_path = first_source_dir / "summaries" / "summary.json"
        source_metadata_path = first_source_dir / "summaries" / "run_metadata.json"
        if not source_summary_path.exists():
            raise BenchmarkSetupError(f"Missing source summary for repeat aggregate: {source_summary_path}")
        source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
        source_metadata = (
            json.loads(source_metadata_path.read_text(encoding="utf-8"))
            if source_metadata_path.exists()
            else {}
        )
        subset = str(source_summary.get("subset", ""))
        model_name = str(source_metadata.get("model_name", ""))
        task_count = int(summary.get("task_count", 0) or 0)
        manifest = dict(source_summary.get("manifest") or {})
        manifest_fingerprint = json.dumps(
            {
                "dataset_id": manifest.get("dataset_id", ""),
                "record_count": manifest.get("record_count", ""),
                "validation_count": manifest.get("validation_count", ""),
                "test_count": manifest.get("test_count", ""),
                "full_count": manifest.get("full_count", ""),
                "metric": manifest.get("metric", ""),
            },
            ensure_ascii=True,
            sort_keys=True,
        )

        subset_values.add(subset)
        model_values.add(model_name)
        task_counts.add(task_count)
        manifest_fingerprints.add(manifest_fingerprint)
        if not manifest_payload:
            manifest_payload = manifest

        per_repeat.append(
            {
                "run_dir": str(run_dir),
                "source_run_dirs": [str(path) for path in source_run_dirs],
                "subset": subset,
                "model_name": model_name,
                "task_count": task_count,
                metric_name: float(summary.get(metric_name, 0.0) or 0.0),
                "average_usd": float(summary.get("average_usd", 0.0) or 0.0),
                "average_input_tokens": float(summary.get("average_input_tokens", 0.0) or 0.0),
                "average_output_tokens": float(summary.get("average_output_tokens", 0.0) or 0.0),
                "average_latency_s": float(summary.get("average_latency_s", 0.0) or 0.0),
            }
        )
        source_repeat_dirs.append(str(run_dir))

    if len(subset_values) != 1:
        raise BenchmarkSetupError(f"Inconsistent subsets across {benchmark} repeats: {sorted(subset_values)}")
    if len(model_values) != 1:
        raise BenchmarkSetupError(f"Inconsistent model names across {benchmark} repeats: {sorted(model_values)}")
    if len(task_counts) != 1:
        raise BenchmarkSetupError(f"Inconsistent task counts across {benchmark} repeats: {sorted(task_counts)}")
    if len(manifest_fingerprints) != 1:
        raise BenchmarkSetupError(f"Inconsistent manifests across {benchmark} repeats")

    summary = {
        "benchmark": benchmark,
        "subset": next(iter(subset_values)),
        "reporting_protocol": "aflow_3run_average",
        "repeat_count": len(per_repeat),
        "task_count": next(iter(task_counts)),
        metric_name: _mean(row[metric_name] for row in per_repeat),
        f"{metric_name}_std": _stddev(row[metric_name] for row in per_repeat),
        "average_usd": _mean(row["average_usd"] for row in per_repeat),
        "average_usd_std": _stddev(row["average_usd"] for row in per_repeat),
        "average_input_tokens": _mean(row["average_input_tokens"] for row in per_repeat),
        "average_input_tokens_std": _stddev(row["average_input_tokens"] for row in per_repeat),
        "average_output_tokens": _mean(row["average_output_tokens"] for row in per_repeat),
        "average_output_tokens_std": _stddev(row["average_output_tokens"] for row in per_repeat),
        "average_latency_s": _mean(row["average_latency_s"] for row in per_repeat),
        "average_latency_s_std": _stddev(row["average_latency_s"] for row in per_repeat),
        "model_name": next(iter(model_values)),
        "manifest": manifest_payload,
        "source_repeat_run_dirs": source_repeat_dirs,
        "per_repeat": per_repeat,
        "run_dir": str(recorder.root),
    }
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_repeat_benchmark_analysis(summary, metric_name))
    return summary


def collect_run_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
    model_cfg = config.get("model", {})
    benchmark_cfg = config.get("benchmark", {})
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "git_commit": _run_quiet(["git", "rev-parse", "HEAD"]),
        "git_branch": _run_quiet(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
        "slurm_partition": os.environ.get("SLURM_JOB_PARTITION", ""),
        "model_name": model_cfg.get("name", ""),
        "model_provider": model_cfg.get("provider", ""),
        "benchmark_kind": benchmark_cfg.get("kind", ""),
        "task_id": config.get("blueprint", {}).get("task", {}).get("task_id", ""),
    }


def detect_runtime_capabilities() -> dict[str, Any]:
    return {
        "commands": {
            command: shutil.which(command) or ""
            for command in ("docker", "repo2run", "wandb", "sbatch", "srun", "sinfo", "squeue")
        },
        "secrets_present": {
            "openai_api_key": Path(".secrets/openai_api_key").exists(),
            "wandb_api_key": Path(".secrets/wandb_api_key").exists(),
        },
    }


def probe_model_access(model_payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        client = OpenAICompatibleLLMClient(timeout_s=60)
        from agentcoop.ir.schema import ModelSpec

        spec = ModelSpec.model_validate(model_payload)
        response = client.complete(
            spec,
            [
                {"role": "system", "content": "Return a JSON object with key ok."},
                {"role": "user", "content": "Say ok=true as JSON."},
            ],
            response_format={"type": "json_object"},
        )
        return {
            "ok": True,
            "content": response.get("content", ""),
            "usage": response.get("usage", {}),
        }
    except (LLMClientError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def build_math_task_blueprint(base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints.update(
        {
            "benchmark": "math",
            "math_type": sample.get("type", ""),
            "difficulty_level": sample.get("level", ""),
        }
    )
    updated_task = base_blueprint.task.model_copy(
        update={
            "task_id": f"{base_blueprint.task.task_id}:{sample['id']}",
            "description": sample["prompt"],
            "hints": task_hints,
        }
    )
    updated_meta = dict(base_blueprint.meta)
    updated_meta.update({"sample_id": sample["id"], "problem_type": sample.get("type", "")})
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


def build_humaneval_task_blueprint(base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints.update(
        {
            "benchmark": "humaneval",
            "entry_point": sample.get("entry_point", ""),
            "task_prompt": sample.get("prompt", ""),
            "public_test_count": len(sample.get("public_tests", []) or []),
        }
    )
    updated_task = base_blueprint.task.model_copy(
        update={
            "task_id": f"{base_blueprint.task.task_id}:{sample['id']}",
            "description": sample["model_prompt"],
            "hints": task_hints,
        }
    )
    updated_meta = dict(base_blueprint.meta)
    updated_meta.update({"sample_id": sample["id"], "entry_point": sample.get("entry_point", "")})
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


def _build_math_task_handlers(
    config: Mapping[str, Any],
    blueprint: WorkflowBlueprint,
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    del sample
    benchmark_settings = dict(config.get("benchmark", {}))
    workflow_variant = str(benchmark_settings.get("workflow_variant", "")).strip().lower()
    node_ids = set(blueprint.node_map())
    if workflow_variant not in {"aflow_v2", "programmer_selector"} and "program_exec" not in node_ids:
        return {}
    handlers: dict[str, Any] = {}
    if "program_exec" in node_ids:
        handlers["program_exec"] = _math_program_exec_handler
    if "selector" in node_ids:
        handlers["selector"] = _math_selector_handler
    return handlers


def _build_humaneval_task_handlers(
    config: Mapping[str, Any],
    blueprint: WorkflowBlueprint,
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    benchmark_settings = dict(config.get("benchmark", {}))
    workflow_variant = str(benchmark_settings.get("workflow_variant", "")).strip().lower()
    node_ids = set(blueprint.node_map())
    if workflow_variant not in {"aflow_v2", "public_test_loop"} and not (
        {"public_test_runner", "retest_runner", "rewrite_test_runner"} & node_ids
    ):
        return {}
    handlers: dict[str, Any] = {}
    for node_id in ("public_test_runner", "retest_runner", "rewrite_test_runner"):
        if node_id in node_ids:
            handlers[node_id] = (
                lambda node, inputs, context, _sample=sample: _humaneval_public_test_handler(
                    node,
                    inputs,
                    context,
                    sample=_sample,
                )
            )
    return handlers


def _sandbox_python_executable(node: NodeSpec, context: Any) -> str:
    sandbox = node.sandbox
    if sandbox is None:
        return sys.executable
    materialized = context.sandbox_runner.ensure_materialized(sandbox)
    if materialized.python_bin:
        return str(materialized.python_bin)
    return sys.executable


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_program_text(payload: Any) -> str:
    candidates: list[str] = []
    if isinstance(payload, Mapping):
        for key in ("program", "python_program", "code", "completion", "final_code", "text"):
            value = payload.get(key)
            if value is None:
                continue
            candidates.append(str(value))
    elif payload is not None:
        candidates.append(str(payload))
    for candidate in candidates:
        cleaned = _strip_code_fences(candidate)
        if cleaned:
            return cleaned
    return ""


def _extract_candidate_completion(inputs: Mapping[str, Any]) -> str:
    direct_value = inputs.get("candidate_code")
    if direct_value is not None:
        cleaned = _strip_code_fences(str(direct_value))
        if cleaned:
            return cleaned
    for key in ("reviser", "coder", "reviewer"):
        value = inputs.get(key)
        if isinstance(value, Mapping):
            for field in ("completion", "corrected_completion", "code", "final_code", "answer", "text"):
                candidate = value.get(field)
                if candidate is None:
                    continue
                cleaned = _strip_code_fences(str(candidate))
                if cleaned:
                    return cleaned
    return ""


def _execute_math_program(
    *,
    program: str,
    python_executable: str,
    timeout_s: int = 20,
) -> dict[str, Any]:
    harness = (
        "import contextlib\n"
        "import io\n"
        "import json\n\n"
        f"PROGRAM = {program!r}\n"
        "payload = {'passed': False, 'executed_answer': '', 'execution_error': '', 'stdout': ''}\n"
        "namespace = {}\n"
        "stdout_buffer = io.StringIO()\n"
        "try:\n"
        "    with contextlib.redirect_stdout(stdout_buffer):\n"
        "        exec(PROGRAM, namespace, namespace)\n"
        "    answer = namespace.get('FINAL_ANSWER')\n"
        "    if answer is None:\n"
        "        answer = namespace.get('final_answer')\n"
        "    if answer is None:\n"
        "        answer = namespace.get('answer')\n"
        "    stdout_text = stdout_buffer.getvalue().strip()\n"
        "    payload['stdout'] = stdout_text[-2000:]\n"
        "    if answer is None and stdout_text:\n"
        "        answer = stdout_text.splitlines()[-1].strip()\n"
        "    if answer is None or not str(answer).strip():\n"
        "        payload['execution_error'] = 'missing_final_answer'\n"
        "    else:\n"
        "        payload['passed'] = True\n"
        "        payload['executed_answer'] = str(answer).strip()\n"
        "except Exception as exc:\n"
        "    payload['stdout'] = stdout_buffer.getvalue()[-2000:]\n"
        "    payload['execution_error'] = f'{exc.__class__.__name__}: {exc}'\n"
        "print(json.dumps(payload, ensure_ascii=True))\n"
    )
    with tempfile.TemporaryDirectory(prefix="agentcoop_math_program_") as tmp_dir:
        script_path = Path(tmp_dir) / "exec.py"
        script_path.write_text(harness, encoding="utf-8")
        try:
            completed = subprocess.run(
                [python_executable, str(script_path)],
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "executed_answer": "",
                "execution_error": "execution timed out",
                "stdout": "",
            }

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        try:
            return json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            pass
    return {
        "passed": False,
        "executed_answer": "",
        "execution_error": stderr or stdout or f"returncode={completed.returncode}",
        "stdout": stdout[-2000:],
    }


def _math_program_exec_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    program = _extract_program_text(inputs)
    candidate_answer = str(inputs.get("candidate_answer", "") or "").strip()
    if not program and isinstance(inputs.get("programmer"), Mapping):
        program = _extract_program_text(inputs["programmer"])
        if not candidate_answer:
            candidate_answer = str(inputs["programmer"].get("final_answer", "") or "").strip()
    if not program:
        return NodeExecutionResult(
            outputs={
                "passed": False,
                "executed_answer": "",
                "candidate_answer": candidate_answer,
                "execution_error": "missing_program",
            },
            trace={"math_program_exec": True, "missing_program": True},
            cost=ExecutionCost(tool_calls=1),
            confidence=0.1,
        )

    python_executable = _sandbox_python_executable(node, context)

    # Retry loop: execute program, and on failure, ask the LLM to fix it
    # (Evidence: AFlow paper — programmer retry with error feedback is the
    # single most impactful technique for MATH benchmark, up to 3 attempts)
    max_retries = int(node.meta.get("max_program_retries", 2))
    total_tool_calls = 0
    retry_trace: list[dict[str, Any]] = []

    for attempt in range(1 + max_retries):
        execution = _execute_math_program(program=program, python_executable=python_executable)
        total_tool_calls += 1
        passed = bool(execution.get("passed", False))
        error = str(execution.get("execution_error", "") or "").strip()

        retry_trace.append({
            "attempt": attempt + 1,
            "passed": passed,
            "error": error[:200] if error else "",
            "program_chars": len(program),
        })

        if passed or attempt >= max_retries:
            break

        # Ask the LLM to fix the program based on the error
        if not error or not hasattr(context, "llm_router"):
            break

        fix_prompt = (
            f"Your Python program failed with this error:\n{error}\n\n"
            f"Original program:\n```python\n{program}\n```\n\n"
            "Fix the bug and return the corrected program. "
            "Return JSON with output.program (the fixed Python code) and output.notes."
        )
        try:
            fix_response = context.llm_router.client.complete(
                model=node.model.name if node.model else "gpt-4o-mini",
                messages=[{"role": "user", "content": fix_prompt}],
                response_format={"type": "json_object"},
            )
            fix_content = fix_response.get("content", "")
            fix_parsed = json.loads(fix_content) if fix_content else {}
            fix_output = fix_parsed.get("output", fix_parsed)
            fixed_program = str(fix_output.get("program", "") or "").strip()
            if fixed_program:
                program = fixed_program
        except Exception:
            break  # LLM fix failed, use last execution result

    synthetic_program = "DYNaforge synthetic program fallback" in program
    outputs = {
        "passed": bool(execution.get("passed", False)),
        "executed_answer": str(execution.get("executed_answer", "") or "").strip(),
        "candidate_answer": candidate_answer,
        "execution_error": str(execution.get("execution_error", "") or "").strip(),
        "stdout": str(execution.get("stdout", "") or "").strip(),
        "program": program,
        "synthetic_program": synthetic_program,
    }
    return NodeExecutionResult(
        outputs=outputs,
        trace={
            "math_program_exec": True,
            "sandbox_python": python_executable,
            "program_chars": len(program),
            "synthetic_program": synthetic_program,
            "retry_attempts": retry_trace,
            "total_attempts": len(retry_trace),
        },
        cost=ExecutionCost(tool_calls=total_tool_calls),
        confidence=0.35 if synthetic_program and outputs["passed"] else (0.92 if outputs["passed"] else 0.25),
    )


def _math_selector_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    del node, context
    task_payload = inputs.get("task", {}) if isinstance(inputs.get("task", {}), Mapping) else {}
    question_text = str(task_payload.get("description", "") or "").strip()

    solver_answer = _sanitize_math_answer_literal(str(inputs.get("solver_answer", "") or "").strip())
    solver_outline = str(inputs.get("solver_outline", "") or "").strip()
    challenger_answer = _sanitize_math_answer_literal(str(inputs.get("challenger_answer", "") or "").strip())
    challenger_outline = str(inputs.get("challenger_outline", "") or "").strip()
    execution_passed = bool(inputs.get("program_execution_passed", False))
    executed_answer = _sanitize_math_answer_literal(str(inputs.get("program_executed_answer", "") or "").strip())
    execution_error = str(inputs.get("program_execution_error", "") or "").strip()
    synthetic_program = bool(inputs.get("program_is_synthetic", False))
    exact_required = _math_question_requires_exact_form(question_text)
    discrete_required = _math_question_prefers_discrete_answer(question_text)
    solver_dubious = _math_answer_looks_dubious(solver_answer, exact_required=exact_required, discrete_required=discrete_required)
    challenger_dubious = _math_answer_looks_dubious(
        challenger_answer,
        exact_required=exact_required,
        discrete_required=discrete_required,
    )
    executed_dubious = _math_answer_looks_dubious(
        executed_answer,
        exact_required=exact_required,
        discrete_required=discrete_required,
    )

    notes: list[str] = []
    needs_review = False
    direct_disagreement = False

    if challenger_answer:
        if not solver_answer:
            solver_answer = challenger_answer
            solver_outline = challenger_outline
            solver_dubious = challenger_dubious
            notes.append("Primary solver answer missing; using challenger answer.")
        elif _math_equal(solver_answer, challenger_answer):
            if _prefer_symbolic_math_answer(challenger_answer, solver_answer):
                solver_answer = challenger_answer
                solver_outline = challenger_outline or solver_outline
                solver_dubious = challenger_dubious
            notes.append("Primary and challenger direct answers agree.")
        else:
            direct_disagreement = True
            notes.append("Primary and challenger direct answers disagree.")
            solver_matches_execution = bool(executed_answer) and _math_equal(solver_answer, executed_answer)
            challenger_matches_execution = bool(executed_answer) and _math_equal(challenger_answer, executed_answer)
            if challenger_matches_execution and not solver_matches_execution:
                solver_answer = challenger_answer
                solver_outline = challenger_outline
                solver_dubious = challenger_dubious
                notes.append("Challenger answer agrees with execution against the primary solver.")
            elif not challenger_dubious and solver_dubious:
                solver_answer = challenger_answer
                solver_outline = challenger_outline
                solver_dubious = challenger_dubious
                notes.append("Challenger answer is structurally cleaner than the primary solver answer.")
            elif exact_required and _prefer_symbolic_math_answer(challenger_answer, solver_answer) and not challenger_dubious:
                solver_answer = challenger_answer
                solver_outline = challenger_outline or solver_outline
                solver_dubious = challenger_dubious
                notes.append("Challenger answer preserves a stronger exact symbolic form.")

    final_answer = solver_answer or executed_answer

    if execution_passed and executed_answer:
        if not solver_answer:
            final_answer = executed_answer
            notes.append("No direct solver answer was available; using executed answer.")
            if executed_dubious:
                needs_review = True
                notes.append("Executed answer shape conflicts with the question requirements.")
        elif _math_equal(executed_answer, solver_answer):
            if _prefer_symbolic_math_answer(solver_answer, executed_answer):
                final_answer = solver_answer
                notes.append("Solver and execution agree numerically; preserving the more exact symbolic form.")
            else:
                final_answer = executed_answer
                notes.append("Solver and execution agree; using executed answer.")
            if synthetic_program:
                needs_review = True
                notes.append("The executed program was synthetic fallback output, so execution is only weak evidence.")
            if executed_dubious and not solver_dubious:
                final_answer = solver_answer
                needs_review = True
                notes.append("Execution agreed numerically but returned a dubious answer shape; preserving solver form.")
        else:
            executed_style = _math_answer_style(executed_answer)
            solver_style = _math_answer_style(solver_answer)
            if exact_required and solver_style == "symbolic" and executed_style == "decimal" and not solver_dubious:
                final_answer = solver_answer
                needs_review = True
                notes.append("The task asks for an exact form, but execution only produced a decimal approximation.")
            elif discrete_required and executed_style == "decimal" and not solver_dubious:
                final_answer = solver_answer
                needs_review = True
                notes.append("The task appears to require a discrete answer, but execution returned a non-integral decimal.")
            elif exact_required and not solver_dubious and not executed_dubious:
                final_answer = solver_answer
                needs_review = True
                notes.append("Exact-answer task with solver/execution disagreement; preserving the exact-form solver answer pending review.")
            elif executed_dubious and not solver_dubious:
                final_answer = solver_answer
                needs_review = True
                notes.append("Executed answer is structurally dubious; preferring solver answer pending review.")
            elif solver_dubious and not executed_dubious:
                final_answer = executed_answer
                needs_review = True
                notes.append("Solver answer is structurally dubious; preferring execution pending review.")
            elif synthetic_program:
                final_answer = solver_answer
                needs_review = True
                notes.append("Execution disagrees with the solver, but the program was synthetic fallback output; keeping solver answer pending review.")
            else:
                final_answer = executed_answer
                needs_review = True
                notes.append("Solver and execution disagree; preferring the executed answer and escalating for review.")
    else:
        final_answer = solver_answer or executed_answer
        if execution_error:
            notes.append(f"Execution failed: {execution_error}")
        if not final_answer:
            notes.append("Neither solver nor execution produced a usable final answer.")
        needs_review = bool(execution_error) or not bool(final_answer)

    if not final_answer and executed_answer:
        final_answer = executed_answer
    if not final_answer:
        final_answer = solver_answer

    if direct_disagreement:
        needs_review = True
        notes.append("Direct reasoning disagreement requires review before trusting the final answer.")

    if solver_outline and not solver_answer:
        notes.append("Solver returned an outline without a final answer.")
    if final_answer and _math_answer_looks_dubious(final_answer, exact_required=exact_required, discrete_required=discrete_required):
        needs_review = True
        notes.append("Chosen answer shape still looks dubious for the task requirements.")

    confidence = 0.93 if execution_passed and not synthetic_program and not needs_review else 0.72
    if not execution_passed:
        confidence = 0.45 if final_answer else 0.2
    if exact_required and _math_answer_style(final_answer) == "decimal":
        confidence = min(confidence, 0.58)
    if discrete_required and _math_answer_style(final_answer) == "decimal":
        confidence = min(confidence, 0.52)

    return NodeExecutionResult(
        outputs={
            "final_answer": final_answer,
            "arbitration_notes": " ".join(note for note in notes if note).strip(),
            "needs_review": needs_review,
        },
        trace={
            "math_selector": True,
            "execution_passed": execution_passed,
            "synthetic_program": synthetic_program,
        },
        cost=ExecutionCost(),
        confidence=confidence,
    )


def _prefer_symbolic_math_answer(primary: str, secondary: str) -> bool:
    if not primary:
        return False
    if not secondary:
        return True
    primary_text = primary.strip().lower()
    secondary_text = secondary.strip().lower()
    primary_symbolic = any(token in primary_text for token in ("pi", "\\pi", "sqrt", "\\sqrt", "/", "^"))
    secondary_symbolic = any(token in secondary_text for token in ("pi", "\\pi", "sqrt", "\\sqrt", "/", "^"))
    if primary_symbolic and not secondary_symbolic:
        return True
    if "." in secondary_text and "." not in primary_text:
        return True
    return False


def _sanitize_math_answer_literal(answer: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    try:
        literal = ast.literal_eval(text)
    except Exception:
        literal = None
    if isinstance(literal, (list, tuple)) and len(literal) == 1:
        return str(literal[0]).strip()
    if isinstance(literal, str):
        return literal.strip()
    if isinstance(literal, (int, float)) and not isinstance(literal, bool):
        numeric = float(literal)
        if abs(numeric - round(numeric)) <= 1e-9:
            return str(int(round(numeric)))
        return str(numeric)
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _math_question_requires_exact_form(question: str) -> bool:
    normalized = str(question or "").lower()
    exact_markers = (
        "exact form",
        "exact value",
        "simplest radical form",
        "in simplest radical form",
        "in exact form",
        "express your answer in exact form",
        "express your answer in simplest form",
        "as a common fraction",
        "in terms of pi",
    )
    return any(marker in normalized for marker in exact_markers)


def _math_question_prefers_discrete_answer(question: str) -> bool:
    normalized = str(question or "").lower()
    discrete_markers = (
        "how many",
        "number of",
        "possible values",
        "integer divisors",
        "positive integers",
        "how many integer",
    )
    return any(marker in normalized for marker in discrete_markers)


def _math_answer_style(answer: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return "empty"
    if text[0] in "[({" and text[-1] in "])}":
        return "container"
    lowered = text.lower()
    if any(token in lowered for token in ("\\pi", "pi", "sqrt", "\\sqrt", "frac", "/", "^")):
        return "symbolic"
    if re.fullmatch(r"-?\d+", text):
        return "integer"
    if re.fullmatch(r"-?\d+\.\d+", text):
        return "decimal"
    if "\\text{" in lowered or re.fullmatch(r"[A-Za-z][A-Za-z\\s-]*", text):
        return "text"
    return "expression"


def _math_answer_looks_dubious(answer: str, *, exact_required: bool, discrete_required: bool) -> bool:
    style = _math_answer_style(answer)
    if style == "empty":
        return True
    if style == "container":
        return True
    if exact_required and style == "decimal":
        return True
    if discrete_required and style == "decimal":
        try:
            return abs(float(answer) - round(float(answer))) > 1e-9
        except Exception:
            return True
    return False


def _humaneval_public_test_handler(
    node: NodeSpec,
    inputs: Mapping[str, Any],
    context: Any,
    *,
    sample: Mapping[str, Any],
) -> NodeExecutionResult:
    completion = _extract_candidate_completion(inputs)
    python_executable = _sandbox_python_executable(node, context)
    result = run_humaneval_public_tests(
        {"completion": completion},
        sample,
        python_executable=python_executable,
    )
    failure_details = _extract_test_failure_details(str(result.get("public_result", "") or ""))
    probe_details = {
        "failing_actual_repr": "",
        "probe_status": "",
        "probe_message": "",
    }
    failing_call = str(failure_details.get("failing_call", "") or "").strip()
    if failing_call:
        probe_details = probe_humaneval_call(
            completion=completion,
            failing_call=failing_call,
            entry_point=str(sample.get("entry_point", "") or ""),
            python_executable=python_executable,
        )
    outputs = {
        "passed": bool(result.get("public_passed", False)),
        "result": str(result.get("public_result", "") or "").strip(),
        "public_test_count": int(result.get("public_test_count", 0) or 0),
        "prediction_code": str(result.get("prediction_code", "") or ""),
        **failure_details,
        **probe_details,
    }
    return NodeExecutionResult(
        outputs=outputs,
        trace={
            "humaneval_public_test_runner": True,
            "sandbox_python": python_executable,
            "entry_point": str(sample.get("entry_point", "") or ""),
        },
        cost=ExecutionCost(tool_calls=1),
        confidence=0.95 if outputs["passed"] else 0.2,
    )


def _extract_test_failure_details(result_text: str) -> dict[str, str]:
    lines = [line.rstrip() for line in str(result_text or "").splitlines() if line.strip()]
    if not lines:
        return {
            "failure_summary": "",
            "failing_assertion": "",
            "failing_call": "",
            "assertion_operator": "",
            "expected_repr": "",
            "exception_type": "",
            "exception_message": "",
        }
    failing_assertion = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("assert ") or "assert candidate(" in stripped:
            failing_assertion = stripped
    last_line = lines[-1].strip()
    exception_type = ""
    exception_message = ""
    if ":" in last_line:
        exception_type, exception_message = [segment.strip() for segment in last_line.split(":", 1)]
    else:
        exception_type = last_line
    failure_summary = exception_type or last_line
    if failing_assertion:
        failure_summary = f"{failure_summary}: {failing_assertion}"
    failing_call = ""
    assertion_operator = ""
    expected_repr = ""
    equality_match = re.search(
        r"assert\s+(candidate\([^)]*\))\s*(==|!=|<=|>=|<|>)\s*(.+?)(?:,\s*\".*\")?$",
        failing_assertion,
    )
    if equality_match:
        failing_call = equality_match.group(1).strip()
        assertion_operator = equality_match.group(2).strip()
        expected_repr = equality_match.group(3).strip()
    else:
        truthy_match = re.search(r"assert\s+(candidate\([^)]*\))(?:,\s*\".*\")?$", failing_assertion)
        if truthy_match:
            failing_call = truthy_match.group(1).strip()
            assertion_operator = "truthy"
    return {
        "failure_summary": failure_summary.strip(),
        "failing_assertion": failing_assertion,
        "failing_call": failing_call,
        "assertion_operator": assertion_operator,
        "expected_repr": expected_repr,
        "exception_type": exception_type,
        "exception_message": exception_message,
    }


def extract_answer_payload(
    report: ExecutionReport,
    *,
    preferred_nodes: Sequence[str],
) -> tuple[Mapping[str, Any], str]:
    for node_id in preferred_nodes:
        result = report.node_results.get(node_id)
        if result is not None and result.outputs:
            return result.outputs, node_id
    for trace in reversed(report.traces):
        if trace.outputs:
            return trace.outputs, trace.node_id
    return {}, ""


def _preferred_answer_nodes(config: Mapping[str, Any], *, default: Sequence[str]) -> list[str]:
    benchmark_settings = dict(config.get("benchmark", {}))
    raw = benchmark_settings.get("preferred_answer_nodes", default)
    return [str(item) for item in raw]


def _resolve_generic_indices(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    subset: str,
    limit: Optional[int],
    task_ids: Optional[Sequence[str]],
) -> list[int]:
    normalized_subset = subset.strip().lower()
    subset_key = "smoke" if normalized_subset == "smoke" else normalized_subset
    if subset_key not in {"smoke", "validation", "test"}:
        raise BenchmarkSetupError(f"Unsupported subset '{subset}'")
    indices = _load_indices(manifest, subset=subset_key)
    if task_ids:
        requested = [task_id.strip() for task_id in task_ids if task_id and task_id.strip()]
        index_by_task = {str(records[index]["id"]): index for index in indices}
        filtered: list[int] = []
        missing: list[str] = []
        for task_id in requested:
            index = index_by_task.get(task_id)
            if index is None:
                missing.append(task_id)
                continue
            if index not in filtered:
                filtered.append(index)
        if missing:
            raise BenchmarkSetupError(
                f"Requested task IDs are not available in the selected subset: {', '.join(sorted(missing))}"
            )
        indices = filtered
    if limit is not None:
        indices = indices[:limit]
    return indices


def _find_report_path(trace_dir: Path, sample_id: str) -> Path | None:
    for suffix in (".report.json.gz", ".report.json"):
        p = trace_dir / f"{sample_id}{suffix}"
        if p.exists():
            return p
    return None


def _load_existing_generic_task_results(
    *,
    run_root: Path,
    records: Sequence[Mapping[str, Any]],
    selected_indices: Sequence[int],
    preferred_nodes: Sequence[str],
    result_builder: Any,
) -> list[dict[str, Any]]:
    task_results_path = run_root / "eval" / "task_results.json"
    if task_results_path.exists():
        payload = json.loads(task_results_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return _sorted_task_results(payload)

    sample_by_id = {str(records[index]["id"]): {"sample": records[index], "sample_index": index} for index in selected_indices}
    reconstructed: list[dict[str, Any]] = []
    for order, index in enumerate(selected_indices, start=1):
        sample = records[index]
        report_path = _find_report_path(run_root / "traces", str(sample["id"]))
        if report_path is None:
            continue
        report = ExecutionReport.model_validate(RunRecorder.read_json(report_path))
        reconstructed.append(
            result_builder(
                sample=sample,
                sample_index=index,
                order=order,
                report=report,
                report_path=report_path,
            )
        )
    return _sorted_task_results(reconstructed)


def _apply_shard(indices: Sequence[int], *, num_shards: int, shard_index: int) -> list[int]:
    if num_shards <= 1:
        return list(indices)
    return [index for position, index in enumerate(indices) if position % num_shards == shard_index]


def _build_math_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    grading = grade_math_prediction(answer_payload, sample)
    selector_outputs = report.node_results.get("selector", NodeExecutionResult()).outputs
    program_exec_outputs = report.node_results.get("program_exec", NodeExecutionResult()).outputs
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample["question"],
        "metadata": {
            "type": sample.get("type", ""),
            "level": sample.get("level", ""),
            "source_config": sample.get("source_config", ""),
        },
        "prediction_node": answer_node,
        **grading,
        "selector_needs_review": bool(selector_outputs.get("needs_review", False))
        if isinstance(selector_outputs, Mapping)
        else False,
        "selector_notes": str(selector_outputs.get("arbitration_notes", "") or "")
        if isinstance(selector_outputs, Mapping)
        else "",
        "program_execution_passed": bool(program_exec_outputs.get("passed", False))
        if isinstance(program_exec_outputs, Mapping)
        else False,
        "program_executed_answer": str(program_exec_outputs.get("executed_answer", "") or "")
        if isinstance(program_exec_outputs, Mapping)
        else "",
        "program_execution_error": str(program_exec_outputs.get("execution_error", "") or "")
        if isinstance(program_exec_outputs, Mapping)
        else "",
        "program_execution_synthetic": bool(program_exec_outputs.get("synthetic_program", False))
        if isinstance(program_exec_outputs, Mapping)
        else False,
        "workflow_signature": _workflow_signature(report),
        "activated_gates": report.activated_gates,
        "active_subgraphs": report.active_subgraphs,
        "activated_node_count": sum(1 for trace in report.traces if trace.status in {"success", "cached"}),
        "activated_subgraph_count": len(report.active_subgraphs),
        "runtime_failure_type": report.failure_type.value,
        "failure_type": report.failure_type.value if report.failure_type.value != "none" else ("incorrect_answer" if not grading["correct"] else "none"),
        "confidence": report.confidence,
        "summary": report.summary,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _build_humaneval_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    grading = grade_humaneval_prediction(answer_payload, sample)
    public_test_outputs = report.node_results.get("public_test_runner", NodeExecutionResult()).outputs
    retest_outputs = report.node_results.get("retest_runner", NodeExecutionResult()).outputs
    rewrite_test_outputs = report.node_results.get("rewrite_test_runner", NodeExecutionResult()).outputs
    reviser_outputs = report.node_results.get("reviser", NodeExecutionResult()).outputs
    rewriter_outputs = report.node_results.get("rewriter", NodeExecutionResult()).outputs
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample["question"],
        "metadata": {"entry_point": sample.get("entry_point", "")},
        "prediction_node": answer_node,
        **grading,
        "public_test_passed": bool(public_test_outputs.get("passed", False))
        if isinstance(public_test_outputs, Mapping)
        else False,
        "public_test_result": str(public_test_outputs.get("result", "") or "")
        if isinstance(public_test_outputs, Mapping)
        else "",
        "retest_passed": bool(retest_outputs.get("passed", False))
        if isinstance(retest_outputs, Mapping)
        else False,
        "retest_result": str(retest_outputs.get("result", "") or "")
        if isinstance(retest_outputs, Mapping)
        else "",
        "rewrite_test_passed": bool(rewrite_test_outputs.get("passed", False))
        if isinstance(rewrite_test_outputs, Mapping)
        else False,
        "rewrite_test_result": str(rewrite_test_outputs.get("result", "") or "")
        if isinstance(rewrite_test_outputs, Mapping)
        else "",
        "repair_changed": bool(reviser_outputs.get("repair_changed", False))
        if isinstance(reviser_outputs, Mapping)
        else (bool(answer_payload.get("repair_changed", False)) if isinstance(answer_payload, Mapping) else False),
        "repair_change_reason": str(reviser_outputs.get("repair_change_reason", "") or "")
        if isinstance(reviser_outputs, Mapping)
        else (str(answer_payload.get("repair_change_reason", "") or "") if isinstance(answer_payload, Mapping) else ""),
        "rewrite_changed": bool(rewriter_outputs.get("rewrite_changed", False))
        if isinstance(rewriter_outputs, Mapping)
        else (bool(answer_payload.get("rewrite_changed", False)) if isinstance(answer_payload, Mapping) else False),
        "rewrite_change_reason": str(rewriter_outputs.get("rewrite_change_reason", "") or "")
        if isinstance(rewriter_outputs, Mapping)
        else (str(answer_payload.get("rewrite_change_reason", "") or "") if isinstance(answer_payload, Mapping) else ""),
        "workflow_signature": _workflow_signature(report),
        "activated_gates": report.activated_gates,
        "active_subgraphs": report.active_subgraphs,
        "activated_node_count": sum(1 for trace in report.traces if trace.status in {"success", "cached"}),
        "activated_subgraph_count": len(report.active_subgraphs),
        "runtime_failure_type": report.failure_type.value,
        "failure_type": report.failure_type.value if report.failure_type.value != "none" else ("incorrect_answer" if not grading["correct"] else "none"),
        "confidence": report.confidence,
        "summary": report.summary,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _build_generic_exception_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    error: Exception,
    benchmark: str,
) -> dict[str, Any]:
    error_type = "tool_runtime_error" if isinstance(error, LLMClientError) else "unknown"
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample.get("question", ""),
        "metadata": {},
        "prediction_node": "",
        "workflow_signature": "",
        "activated_gates": [],
        "active_subgraphs": [],
        "activated_node_count": 0,
        "activated_subgraph_count": 0,
        "runtime_failure_type": error_type,
        "failure_type": error_type,
        "confidence": 0.0,
        "summary": f"{benchmark} runner error: {error}",
        "report_path": "",
        "cost": {},
        "correct": False,
        "error_class": type(error).__name__,
        "error": str(error),
    }


def _summarize_generic_task_results(
    *,
    benchmark: str,
    metric_name: str,
    task_results: Sequence[Mapping[str, Any]],
    subset: str,
    manifest: Mapping[str, Any],
    requested_task_ids: Sequence[str],
    num_shards: int,
    shard_index: int,
) -> dict[str, Any]:
    workflow_counter: Counter[str] = Counter()
    failure_counter: Counter[str] = Counter()
    gate_counter: Counter[str] = Counter()
    total_usd = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency = 0.0
    total_activated_nodes = 0
    total_activated_subgraphs = 0

    for result in task_results:
        workflow_counter[str(result.get("workflow_signature", ""))] += 1
        failure_counter[str(result.get("failure_type", "unknown"))] += 1
        for gate_id in result.get("activated_gates", []):
            gate_counter[str(gate_id)] += 1
        cost = result.get("cost", {})
        if isinstance(cost, Mapping):
            total_usd += float(cost.get("usd", 0.0) or 0.0)
            total_input_tokens += int(cost.get("input_tokens", 0) or 0)
            total_output_tokens += int(cost.get("output_tokens", 0) or 0)
            total_latency += float(cost.get("wall_time_s", 0.0) or 0.0)
        total_activated_nodes += int(result.get("activated_node_count", 0) or 0)
        total_activated_subgraphs += int(result.get("activated_subgraph_count", 0) or 0)

    # For F1-metric benchmarks (HotpotQA, DROP), use average F1 as primary metric
    # (matching AFlow's reporting convention)
    if metric_name == "f1":
        f1_values = [float(r.get("f1", 0.0) or 0.0) for r in task_results]
        metric_value = _safe_ratio(sum(f1_values), len(task_results)) if task_results else 0.0
        em_rate = _safe_ratio(sum(1 for r in task_results if r.get("exact_match")), len(task_results))
        correct_rate = _safe_ratio(sum(1 for r in task_results if _is_generic_result_correct(benchmark, r)), len(task_results))
    else:
        metric_value = _safe_ratio(sum(1 for result in task_results if _is_generic_result_correct(benchmark, result)), len(task_results))
        em_rate = metric_value
        correct_rate = metric_value
    return {
        "benchmark": benchmark,
        "subset": subset,
        "requested_task_ids": list(requested_task_ids),
        "num_shards": num_shards,
        "shard_index": shard_index,
        "task_count": len(task_results),
        metric_name: metric_value,
        "accuracy": metric_value,
        "exact_match_rate": em_rate,
        "correct_rate": correct_rate,
        "average_usd": _safe_ratio(total_usd, len(task_results)),
        "average_input_tokens": _safe_ratio(total_input_tokens, len(task_results)),
        "average_output_tokens": _safe_ratio(total_output_tokens, len(task_results)),
        "average_latency_s": _safe_ratio(total_latency, len(task_results)),
        "average_activated_nodes": _safe_ratio(total_activated_nodes, len(task_results)),
        "average_activated_subgraphs": _safe_ratio(total_activated_subgraphs, len(task_results)),
        "workflow_patterns": dict(workflow_counter.most_common()),
        "failure_breakdown": dict(failure_counter),
        "gate_activations": dict(gate_counter),
        "manifest": {
            "dataset_id": manifest.get("dataset_id", ""),
            "record_count": manifest.get("record_count", 0),
            "validation_count": manifest.get("validation_count", 0),
            "test_count": manifest.get("test_count", 0),
            "metric": manifest.get("metric", metric_name),
        },
    }


def render_generic_benchmark_analysis(summary: Mapping[str, Any], task_results: Sequence[Mapping[str, Any]]) -> str:
    metric_name = "pass_at_1" if summary.get("benchmark") == "humaneval" else "solve_rate"
    metric_value = float(summary.get(metric_name, summary.get("accuracy", 0.0)) or 0.0)
    lines = [
        f"# {summary.get('benchmark', 'benchmark')} {summary.get('subset', '')} analysis",
        "",
        f"- Task count: {summary.get('task_count', 0)}",
        f"- {metric_name}: {metric_value:.3f}",
        f"- Average USD: {float(summary.get('average_usd', 0.0) or 0.0):.6f}",
        f"- Workflow patterns: {json.dumps(summary.get('workflow_patterns', {}), ensure_ascii=True)}",
        f"- Failure breakdown: {json.dumps(summary.get('failure_breakdown', {}), ensure_ascii=True)}",
        "",
        "## Sample Failures",
    ]
    benchmark = str(summary.get("benchmark", ""))
    failures = [item for item in task_results if not _is_generic_result_correct(benchmark, item)]
    if not failures:
        lines.append("- None")
    else:
        for item in failures[:10]:
            lines.append(
                f"- `{item['task_id']}`: failure_type={item.get('failure_type', 'unknown')} "
                f"prediction_node={item.get('prediction_node', '')} workflow={item.get('workflow_signature', '')}"
            )
    return "\n".join(lines) + "\n"


def _is_generic_result_correct(benchmark: str, result: Mapping[str, Any]) -> bool:
    if benchmark == "math":
        prediction_answer = result.get("prediction_answer")
        gold_answer = result.get("gold_answer")
        if prediction_answer is not None or gold_answer is not None:
            return bool(
                grade_math_prediction(
                    {"final_answer": prediction_answer},
                    {"gold_answer": gold_answer},
                ).get("correct")
            )
    return bool(result.get("correct"))


def _sorted_task_results(task_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(result)
        for result in sorted(
            task_results,
            key=lambda item: (
                int(item.get("order", 0) or 0),
                int(item.get("sample_index", 0) or 0),
                str(item.get("task_id", "")),
            ),
        )
    ]


def _build_scanpy_case_sandbox(
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
) -> SandboxSpec:
    sandbox_payload = dict(benchmark_settings.get("sandbox", {}))
    repo2run_payload = dict(sandbox_payload.get("repo2run", {}))
    scanpy_repo_path = ""
    for repo in case_assets.get("candidate_repos", []):
        if str(repo.get("name", "")) == "scanpy":
            scanpy_repo_path = str(repo.get("path", ""))
            break
    if not scanpy_repo_path:
        raise BenchmarkSetupError("Case-study manifest is missing the cloned scanpy candidate repo")

    repo2run_payload.setdefault("source", scanpy_repo_path)
    sandbox_payload["repo2run"] = repo2run_payload
    sandbox_payload.setdefault("sandbox_id", "scanpy-pbmc3k")
    sandbox_payload.setdefault("image", "python:3.11")
    sandbox_payload.setdefault("backend", SandboxBackend.local_venv)
    bootstrap_cmds = list(sandbox_payload.get("bootstrap_cmds", []))
    if not bootstrap_cmds:
        bootstrap_cmds = [["python", "-m", "pip", "install", "leidenalg", "igraph", "pillow", "mcp"]]
    sandbox_payload["bootstrap_cmds"] = bootstrap_cmds
    return SandboxSpec.model_validate(sandbox_payload)


def _build_squidpy_case_sandbox(
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
) -> SandboxSpec:
    sandbox_payload = dict(benchmark_settings.get("sandbox", {}))
    repo2run_payload = dict(sandbox_payload.get("repo2run", {}))
    squidpy_repo_path = ""
    for repo in case_assets.get("candidate_repos", []):
        if str(repo.get("name", "")) == "squidpy":
            squidpy_repo_path = str(repo.get("path", ""))
            break
    if not squidpy_repo_path:
        raise BenchmarkSetupError("Case-study manifest is missing the cloned squidpy candidate repo")

    repo2run_payload.setdefault("source", squidpy_repo_path)
    sandbox_payload["repo2run"] = repo2run_payload
    sandbox_payload.setdefault("sandbox_id", "squidpy-visium")
    sandbox_payload.setdefault("image", "python:3.11")
    sandbox_payload.setdefault("backend", SandboxBackend.local_venv)
    bootstrap_cmds = list(sandbox_payload.get("bootstrap_cmds", []))
    if not bootstrap_cmds:
        if str(case_assets.get("case_id", "")).startswith("visium_multi_agent_"):
            bootstrap_cmds = [["python", "-m", "pip", "install", "pillow", "mcp", "leidenalg", "igraph"]]
        else:
            bootstrap_cmds = [["python", "-m", "pip", "install", "pillow", "mcp"]]
    sandbox_payload["bootstrap_cmds"] = bootstrap_cmds
    return SandboxSpec.model_validate(sandbox_payload)


def _ensure_scanpy_case_reference_assets(
    *,
    case_assets: Mapping[str, Any],
    benchmark_settings: Mapping[str, Any],
    sandbox: SandboxSpec,
    sandbox_runner: Any,
) -> dict[str, Any]:
    reference_figure_path = Path(str(case_assets["reference_figure_path"])).resolve()
    reference_summary_path = Path(str(case_assets["reference_summary_path"])).resolve()
    raw_data_path = Path(str(case_assets["raw_data_path"])).resolve()
    if reference_figure_path.exists() and reference_summary_path.exists() and raw_data_path.exists():
        return {
            "reference_figure_path": str(reference_figure_path),
            "reference_summary_path": str(reference_summary_path),
            "raw_data_path": str(raw_data_path),
            "generated": False,
        }

    analysis_config = benchmark_settings.get("analysis_config", {})
    completed = sandbox_runner.run_in_sandbox(
        sandbox,
        [
            "python",
            "-m",
            "agentcoop.case_studies.scanpy_pbmc3k_job",
            "--output-dir",
            str(reference_figure_path.parent),
            "--figure-path",
            str(reference_figure_path),
            "--summary-path",
            str(reference_summary_path),
            "--raw-data-path",
            str(raw_data_path),
            "--selected-repo",
            "scanpy",
            "--config-json",
            json.dumps(analysis_config, ensure_ascii=True),
            "--title",
            "PBMC3k Reference UMAP",
        ],
        timeout_s=int(sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        raise BenchmarkSetupError(
            f"Failed to generate scanpy PBMC3k reference assets: {completed.stderr or completed.stdout}"
        )
    return {
        "reference_figure_path": str(reference_figure_path),
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "generated": True,
        "stdout": completed.stdout,
    }


def _ensure_squidpy_case_reference_assets(
    *,
    case_assets: Mapping[str, Any],
    benchmark_settings: Mapping[str, Any],
    sandbox: SandboxSpec,
    sandbox_runner: Any,
) -> dict[str, Any]:
    case_id = str(case_assets.get("case_id", "squidpy_visium_hne_spatial"))
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    interaction_case = case_id in {"squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}
    reference_figure_path = Path(str(case_assets["reference_figure_path"])).resolve()
    reference_summary_path = Path(str(case_assets["reference_summary_path"])).resolve()
    raw_data_path = Path(str(case_assets["raw_data_path"])).resolve()
    reference_interaction_figure = Path(str(case_assets.get("reference_interaction_figure_path", "")).strip()).resolve() if str(case_assets.get("reference_interaction_figure_path", "")).strip() else None
    interaction_required = reference_interaction_figure is not None
    if (
        reference_figure_path.exists()
        and reference_summary_path.exists()
        and raw_data_path.exists()
        and (not interaction_required or reference_interaction_figure.exists())
    ):
        payload = {
            "reference_figure_path": str(reference_figure_path),
            "reference_summary_path": str(reference_summary_path),
            "raw_data_path": str(raw_data_path),
            "generated": False,
        }
        if interaction_required and reference_interaction_figure is not None:
            payload["reference_interaction_figure_path"] = str(reference_interaction_figure)
        return payload

    analysis_config = benchmark_settings.get("analysis_config", {})
    job_module = (
        "agentcoop.case_studies.squidpy_visium_interactions_job"
        if interaction_case
        else "agentcoop.case_studies.squidpy_visium_job"
    )
    cmd = [
        "python",
        "-m",
        job_module,
        "--output-dir",
        str(reference_figure_path.parent),
        "--figure-path",
        str(reference_figure_path),
        "--summary-path",
        str(reference_summary_path),
        "--raw-data-path",
        str(raw_data_path),
        "--selected-repo",
        "squidpy",
        "--config-json",
        json.dumps(analysis_config, ensure_ascii=True),
        "--title",
        f"{dataset_label} Reference Interaction Analysis" if interaction_case else f"{dataset_label} Reference Spatial Plot",
    ]
    if interaction_required and reference_interaction_figure is not None:
        cmd.extend(["--interaction-figure-path", str(reference_interaction_figure)])
    completed = sandbox_runner.run_in_sandbox(
        sandbox,
        cmd,
        timeout_s=int(sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        raise BenchmarkSetupError(
            f"Failed to generate Squidpy reference assets: {completed.stderr or completed.stdout}"
        )
    payload = {
        "reference_figure_path": str(reference_figure_path),
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "generated": True,
        "stdout": completed.stdout,
    }
    if interaction_required and reference_interaction_figure is not None:
        payload["reference_interaction_figure_path"] = str(reference_interaction_figure)
    return payload


def _ensure_scanpy_paul15_reference_assets(
    *,
    case_assets: Mapping[str, Any],
    benchmark_settings: Mapping[str, Any],
    sandbox: SandboxSpec,
    sandbox_runner: Any,
) -> dict[str, Any]:
    requires_gene_panel = bool(str(case_assets.get("reference_gene_trend_figure_path", "")).strip())
    dataset_label = str(case_assets.get("dataset_label", "Paul15"))
    reference_figure_path = Path(str(case_assets["reference_figure_path"])).resolve()
    reference_paga_figure_path = Path(str(case_assets["reference_paga_figure_path"])).resolve()
    reference_gene_trend_figure = (
        Path(str(case_assets.get("reference_gene_trend_figure_path", "")).strip()).resolve()
        if str(case_assets.get("reference_gene_trend_figure_path", "")).strip()
        else None
    )
    reference_summary_path = Path(str(case_assets["reference_summary_path"])).resolve()
    raw_data_path = Path(str(case_assets["raw_data_path"])).resolve()
    if (
        reference_figure_path.exists()
        and reference_paga_figure_path.exists()
        and reference_summary_path.exists()
        and raw_data_path.exists()
        and (not requires_gene_panel or (reference_gene_trend_figure is not None and reference_gene_trend_figure.exists()))
    ):
        payload = {
            "reference_figure_path": str(reference_figure_path),
            "reference_paga_figure_path": str(reference_paga_figure_path),
            "reference_summary_path": str(reference_summary_path),
            "raw_data_path": str(raw_data_path),
            "generated": False,
        }
        if reference_gene_trend_figure is not None:
            payload["reference_gene_trend_figure_path"] = str(reference_gene_trend_figure)
        return payload

    analysis_config = benchmark_settings.get("analysis_config", {})
    cmd = [
        "python",
        "-m",
        "agentcoop.case_studies.scanpy_paul15_job",
        "--output-dir",
        str(reference_figure_path.parent),
        "--figure-path",
        str(reference_figure_path),
        "--paga-figure-path",
        str(reference_paga_figure_path),
        "--summary-path",
        str(reference_summary_path),
        "--raw-data-path",
        str(raw_data_path),
        "--selected-repo",
        "scanpy",
        "--config-json",
        json.dumps(analysis_config, ensure_ascii=True),
    ]
    if requires_gene_panel and reference_gene_trend_figure is not None:
        cmd.extend(["--gene-trend-figure-path", str(reference_gene_trend_figure)])
    figure_title = f"{dataset_label} Reference Trajectory"
    paga_title = f"{dataset_label} Reference PAGA Graph"
    completed = sandbox_runner.run_in_sandbox(
        sandbox,
        cmd + ["--title", figure_title, "--paga-title", paga_title],
        timeout_s=int(sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        raise BenchmarkSetupError(
            f"Failed to generate Scanpy trajectory reference assets: {completed.stderr or completed.stdout}"
        )
    payload = {
        "reference_figure_path": str(reference_figure_path),
        "reference_paga_figure_path": str(reference_paga_figure_path),
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "generated": True,
        "stdout": completed.stdout,
    }
    if reference_gene_trend_figure is not None:
        payload["reference_gene_trend_figure_path"] = str(reference_gene_trend_figure)
    return payload


def _ensure_visium_multi_agent_reference_assets(
    *,
    case_assets: Mapping[str, Any],
    benchmark_settings: Mapping[str, Any],
    sandbox: SandboxSpec,
    sandbox_runner: Any,
) -> dict[str, Any]:
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    reference_cluster_figure_path = Path(str(case_assets["reference_cluster_figure_path"])).resolve()
    reference_marker_figure_path = Path(str(case_assets["reference_marker_figure_path"])).resolve()
    reference_spatial_figure_path = Path(str(case_assets["reference_spatial_figure_path"])).resolve()
    reference_interaction_figure_path = Path(str(case_assets["reference_interaction_figure_path"])).resolve()
    reference_cluster_summary_path = Path(str(case_assets["reference_cluster_summary_path"])).resolve()
    reference_interaction_summary_path = Path(str(case_assets["reference_interaction_summary_path"])).resolve()
    reference_summary_path = Path(str(case_assets["reference_summary_path"])).resolve()
    raw_data_path = Path(str(case_assets["raw_data_path"])).resolve()
    if (
        reference_cluster_figure_path.exists()
        and reference_marker_figure_path.exists()
        and reference_spatial_figure_path.exists()
        and reference_interaction_figure_path.exists()
        and reference_cluster_summary_path.exists()
        and reference_interaction_summary_path.exists()
        and reference_summary_path.exists()
        and raw_data_path.exists()
    ):
        return {
            "reference_cluster_figure_path": str(reference_cluster_figure_path),
            "reference_marker_figure_path": str(reference_marker_figure_path),
            "reference_spatial_figure_path": str(reference_spatial_figure_path),
            "reference_interaction_figure_path": str(reference_interaction_figure_path),
            "reference_cluster_summary_path": str(reference_cluster_summary_path),
            "reference_interaction_summary_path": str(reference_interaction_summary_path),
            "reference_summary_path": str(reference_summary_path),
            "raw_data_path": str(raw_data_path),
            "generated": False,
        }

    cluster_config = benchmark_settings.get("cluster_analysis_config", {})
    interaction_config = benchmark_settings.get("interaction_analysis_config", {})
    reference_annotated_path = reference_cluster_figure_path.parent / "reference_annotated_visium.h5ad"
    completed = sandbox_runner.run_in_sandbox(
        sandbox,
        [
            "python",
            "-m",
            "agentcoop.case_studies.visium_multi_agent_monolith_job",
            "--output-dir",
            str(reference_cluster_figure_path.parent),
            "--cluster-figure-path",
            str(reference_cluster_figure_path),
            "--marker-figure-path",
            str(reference_marker_figure_path),
            "--cluster-summary-path",
            str(reference_cluster_summary_path),
            "--annotated-data-path",
            str(reference_annotated_path),
            "--spatial-figure-path",
            str(reference_spatial_figure_path),
            "--interaction-figure-path",
            str(reference_interaction_figure_path),
            "--interaction-summary-path",
            str(reference_interaction_summary_path),
            "--summary-path",
            str(reference_summary_path),
            "--raw-data-path",
            str(raw_data_path),
            "--scanpy-repo",
            "scanpy",
            "--squidpy-repo",
            "squidpy",
            "--cluster-config-json",
            json.dumps(cluster_config, ensure_ascii=True),
            "--interaction-config-json",
            json.dumps(interaction_config, ensure_ascii=True),
            "--cluster-title",
            f"{dataset_label} Scanpy Clusters",
            "--marker-title",
            f"{dataset_label} Marker Heatmap",
            "--spatial-title",
            f"{dataset_label} Spatial Layout",
            "--interaction-title",
            f"{dataset_label} Neighborhood Enrichment",
        ],
        timeout_s=int(sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        raise BenchmarkSetupError(
            f"Failed to generate spatial multi-agent reference assets: {completed.stderr or completed.stdout}"
        )
    return {
        "reference_cluster_figure_path": str(reference_cluster_figure_path),
        "reference_marker_figure_path": str(reference_marker_figure_path),
        "reference_spatial_figure_path": str(reference_spatial_figure_path),
        "reference_interaction_figure_path": str(reference_interaction_figure_path),
        "reference_cluster_summary_path": str(reference_cluster_summary_path),
        "reference_interaction_summary_path": str(reference_interaction_summary_path),
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "generated": True,
        "stdout": completed.stdout,
    }


def _case_review_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "methodology_faithfulness",
            "biological_plausibility",
            "artifact_completeness",
            "overall_verdict",
            "concerns",
        ],
        "properties": {
            "methodology_faithfulness": {"type": "number"},
            "biological_plausibility": {"type": "number"},
            "artifact_completeness": {"type": "number"},
            "overall_verdict": {"type": "string"},
            "concerns": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    }


def _paper_specialist_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["required_artifacts", "required_summary_fields", "scientific_focus", "summary"],
        "properties": {
            "required_artifacts": {"type": "array", "items": {"type": "string"}},
            "required_summary_fields": {"type": "array", "items": {"type": "string"}},
            "scientific_focus": {"type": "string"},
            "summary": {"type": "string"},
            "target_figure_description": {"type": "string"},
        },
    }


def _paper_figure_critic_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["methodology_faithfulness", "biological_plausibility", "artifact_completeness", "concerns"],
        "properties": {
            "methodology_faithfulness": {"type": "number"},
            "biological_plausibility": {"type": "number"},
            "artifact_completeness": {"type": "number"},
            "should_refine": {"type": "boolean"},
            "concerns": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
            "required_artifacts": {"type": "array", "items": {"type": "string"}},
        },
    }


def _case_refiner_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["selected_repo", "analysis_config", "summary"],
        "properties": {
            "selected_repo": {"type": "string"},
            "analysis_config": {"type": "object"},
            "summary": {"type": "string"},
            "rationale": {"type": "string"},
        },
    }


def _visium_multi_agent_plan_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "scanpy_repo",
            "cluster_analysis_config",
            "squidpy_repo",
            "interaction_analysis_config",
            "summary",
        ],
        "properties": {
            "scanpy_repo": {"type": "string"},
            "cluster_analysis_config": {"type": "object"},
            "squidpy_repo": {"type": "string"},
            "interaction_analysis_config": {"type": "object"},
            "summary": {"type": "string"},
        },
    }


def _build_scanpy_case_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
    recorder: RunRecorder,
    sandbox: SandboxSpec,
) -> WorkflowBlueprint:
    model_spec = ModelSpec.model_validate(config.get("model", {}))
    review_payload = config.get("review_model", benchmark_settings.get("review_model", config.get("model", {})))
    review_model = ModelSpec.model_validate(review_payload)
    blueprint_cfg = config.get("blueprint", {})
    budget_payload = dict(blueprint_cfg.get("budget", {})) if isinstance(blueprint_cfg, Mapping) else {}
    budget = BudgetSpec.model_validate(budget_payload or {})
    dataset_id = str(case_assets.get("dataset_id", "visium_hne_adata_crop")).strip() or "visium_hne_adata_crop"
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))

    output_dir = recorder.artifacts_dir / "generated"
    figure_path = output_dir / "generated_umap.png"
    summary_path = output_dir / "generated_summary.json"
    raw_data_path = recorder.artifacts_dir / "pbmc3k_raw.h5ad"
    task_description = "\n\n".join(
        [
            "Reproduce the target PBMC3k UMAP figure as faithfully as possible using one of the candidate repos.",
            f"Methods excerpt:\n{methods_excerpt}",
            f"Target figure description:\n{target_description}",
            "Candidate repos:\n" + "\n".join(f"- {item['name']}: {item['url']}" for item in candidate_repos),
        ]
    )
    task_hints = {
        "case_id": case_assets["case_id"],
        "methods_excerpt_path": str(case_assets["methods_excerpt_path"]),
        "target_figure_description": target_description,
        "candidate_repos": candidate_repos,
        "analysis_config": dict(benchmark_settings.get("analysis_config", {})),
        "output_dir": str(output_dir.resolve()),
        "figure_path": str(figure_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "raw_data_path": str(raw_data_path.resolve()),
        "reference_figure_path": str(reference_assets["reference_figure_path"]),
        "reference_summary_path": str(reference_assets["reference_summary_path"]),
        "figure_title": "PBMC3k UMAP",
    }
    enable_refinement = bool(benchmark_settings.get("enable_refinement_subgraph", True))
    refinement_threshold = float(benchmark_settings.get("refinement_threshold", 8.0))

    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="BioPlanner",
        description="Select the best candidate repo and a concrete Scanpy analysis configuration for PBMC3k UMAP reproduction.",
        model=model_spec,
        system_prompt=(
            "You are a biological workflow planner. Choose the most appropriate candidate repo for execution, "
            "stay close to the methods excerpt, and output JSON with keys selected_repo, analysis_config, and summary. "
            "selected_repo must be one of the provided candidate repo names."
        ),
        io=IOContract(output_schema=_case_refiner_output_schema()),
        max_steps=1,
    )
    tool_node = NodeSpec(
        node_id="scanpy_tool",
        kind=NodeKind.tool,
        role="ScanpySandboxAgent",
        description="Run the PBMC3k Scanpy UMAP pipeline inside an isolated sandbox via MCP.",
        sandbox=sandbox,
        tools=[ToolRef(server="scanpy_pbmc3k", tool="generate_umap")],
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["result"],
                "properties": {
                    "result": {
                        "type": "object",
                        "required": ["status", "summary"],
                        "properties": {
                            "status": {"type": "string"},
                            "summary": {"type": "object"},
                        },
                    }
                },
            },
        ),
    )
    reviewer = NodeSpec(
        node_id="bio_reviewer",
        kind=NodeKind.evaluator,
        role="BioReviewer",
        description="Assess methodological faithfulness and biological plausibility of the reproduced PBMC3k figure.",
        model=review_model,
        system_prompt=(
            "You are reviewing a single-cell PBMC3k UMAP reproduction. "
            "Use the methods excerpt, target description, and generated summary to score methodology_faithfulness, "
            "biological_plausibility, and artifact_completeness on a 1-10 scale. "
            "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, "
            "overall_verdict, concerns, and summary."
        ),
        io=IOContract(output_schema=_case_review_output_schema()),
    )
    subgraphs: list[SubgraphSpec] = []
    gates: list[GateSpec] = []
    if enable_refinement:
        refiner = NodeSpec(
            node_id="case_refiner",
            kind=NodeKind.agent,
            role="CaseRefiner",
            description="Revise the analysis plan after reviewer concerns while staying close to the target methods.",
            model=model_spec,
            system_prompt=(
                "You are refining a PBMC3k case-study plan after a reviewer found issues. "
                "You receive the original selected_repo, analysis_config, tool_result, and review_result. "
                "Revise the analysis_config conservatively to address the concerns while keeping the methods faithful. "
                "Return JSON with keys selected_repo, analysis_config, summary, and rationale. "
                "selected_repo must remain one of the candidate repo names."
            ),
            io=IOContract(output_schema=_case_refiner_output_schema()),
            max_steps=1,
        )
        refined_tool = tool_node.model_copy(
            deep=True,
            update={
                "node_id": "scanpy_tool_refined",
                "description": "Re-run the PBMC3k Scanpy UMAP pipeline after reviewer-guided refinement.",
            },
        )
        refined_reviewer = reviewer.model_copy(
            deep=True,
            update={
                "node_id": "bio_reviewer_refined",
                "role": "BioReviewerRefined",
                "description": "Assess the refined PBMC3k reproduction after a second tool pass.",
            },
        )
        subgraphs.append(
            SubgraphSpec(
                subgraph_id="case_refinement",
                purpose="Reviewer-triggered case-study refinement after low biological or methodological scores.",
                nodes=[refiner, refined_tool, refined_reviewer],
                edges=[
                    EdgeSpec(
                        edge_id="edge_planner_to_case_refiner",
                        src="planner",
                        dst="case_refiner",
                        mapping={
                            "selected_repo": "selected_repo",
                            "analysis_config": "analysis_config",
                            "planner_summary": "summary",
                        },
                    ),
                    EdgeSpec(
                        edge_id="edge_tool_to_case_refiner",
                        src="scanpy_tool",
                        dst="case_refiner",
                        mapping={"tool_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_reviewer_to_case_refiner",
                        src="bio_reviewer",
                        dst="case_refiner",
                        mapping={"review_result": "*"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refiner_to_refined_tool",
                        src="case_refiner",
                        dst="scanpy_tool_refined",
                        mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refined_tool_to_refined_reviewer",
                        src="scanpy_tool_refined",
                        dst="bio_reviewer_refined",
                        mapping={"tool_result": "result"},
                    ),
                ],
                entry_nodes=["case_refiner"],
                exit_nodes=["bio_reviewer_refined"],
                default_enabled=False,
            )
        )
        gates.append(
            GateSpec(
                gate_id="case_refinement_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="bio_reviewer"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(
                                    field="node.outputs.methodology_faithfulness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.biological_plausibility",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.artifact_completeness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["case_refinement"],
                max_activations=1,
            )
        )
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_scanpy_pbmc3k_case.py"
    return WorkflowBlueprint(
        task=TaskSpec(
            task_id="scanpy_pbmc3k_case",
            title=str(benchmark_settings.get("title", "Scanpy PBMC3k UMAP reproduction")),
            description=task_description,
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="scanpy_pbmc3k",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.scanpy_pbmc3k_server"],
                sandbox=sandbox,
            )
        ],
        base_nodes=[planner, tool_node, reviewer],
        base_edges=[
            EdgeSpec(
                edge_id="edge_planner_to_tool",
                src="planner",
                dst="scanpy_tool",
                mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
            ),
            EdgeSpec(
                edge_id="edge_tool_to_reviewer",
                src="scanpy_tool",
                dst="bio_reviewer",
                mapping={"tool_result": "result"},
            ),
        ],
        subgraphs=subgraphs,
        gates=gates,
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="scanpy_pbmc3k_hard_check",
                    description="Validate generated PBMC3k UMAP figure and summary against the reference summary.",
                    cmd=[
                        sys.executable,
                        str(script_path),
                        "--figure",
                        str(figure_path.resolve()),
                        "--summary",
                        str(summary_path.resolve()),
                        "--reference-summary",
                        str(reference_assets["reference_summary_path"]),
                    ],
                    timeout_s=120,
                )
            ],
            trace_assertions=[
                {
                    "assertion_id": "tool_invoked",
                    "description": "The sandboxed Scanpy tool must be invoked at least once.",
                    "expr": "trace.tool_calls >= 1 and 'scanpy_tool' in trace.successful_nodes",
                },
                {
                    "assertion_id": "sandbox_materialized",
                    "description": "The sandbox backend should be materialized during the run.",
                    "expr": "trace.container_starts >= 1",
                },
            ],
            soft_judges=[],
        ),
        meta={
            "case_id": case_assets["case_id"],
            "reference_assets": dict(reference_assets),
            "candidate_repo_count": len(candidate_repos),
        },
    )


def _build_scanpy_paul15_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
    recorder: RunRecorder,
    sandbox: SandboxSpec,
) -> WorkflowBlueprint:
    requires_gene_panel = bool(str(case_assets.get("reference_gene_trend_figure_path", "")).strip())
    dataset_id = str(case_assets.get("dataset_id", "paul15")).strip() or "paul15"
    dataset_label = str(case_assets.get("dataset_label", "Paul15")).strip() or "Paul15"
    transfer_case = dataset_id != "paul15"
    model_spec = ModelSpec.model_validate(config.get("model", {}))
    review_payload = config.get("review_model", benchmark_settings.get("review_model", config.get("model", {})))
    review_model = ModelSpec.model_validate(review_payload)
    blueprint_cfg = config.get("blueprint", {})
    budget_payload = dict(blueprint_cfg.get("budget", {})) if isinstance(blueprint_cfg, Mapping) else {}
    budget = BudgetSpec.model_validate(budget_payload or {})
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))

    output_dir = recorder.artifacts_dir / "generated"
    figure_path = output_dir / "generated_trajectory.png"
    paga_figure_path = output_dir / "generated_paga.png"
    gene_trend_figure_path = output_dir / "generated_dynamic_genes.png"
    summary_path = output_dir / "generated_summary.json"
    raw_data_path = recorder.artifacts_dir / f"{dataset_id}_raw.h5ad"
    task_description = "\n\n".join(
        [
            (
                f"Transfer the target {dataset_label} trajectory workflow as faithfully as possible using one of the candidate repos."
                if transfer_case
                else f"Reconstruct the target {dataset_label} trajectory workflow as faithfully as possible using one of the candidate repos."
            ),
            f"Methods excerpt:\n{methods_excerpt}",
            f"Target figure description:\n{target_description}",
            "Candidate repos:\n" + "\n".join(f"- {item['name']}: {item['url']}" for item in candidate_repos),
        ]
    )
    task_hints = {
        "case_id": case_assets["case_id"],
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(case_assets["methods_excerpt_path"]),
        "target_figure_description": target_description,
        "candidate_repos": candidate_repos,
        "analysis_config": dict(benchmark_settings.get("analysis_config", {})),
        "planner_mode": str(benchmark_settings.get("planner_mode", "llm_first")),
        "output_dir": str(output_dir.resolve()),
        "figure_path": str(figure_path.resolve()),
        "paga_figure_path": str(paga_figure_path.resolve()),
        "gene_trend_figure_path": str(gene_trend_figure_path.resolve()) if requires_gene_panel else "",
        "summary_path": str(summary_path.resolve()),
        "raw_data_path": str(raw_data_path.resolve()),
        "reference_figure_path": str(reference_assets["reference_figure_path"]),
        "reference_paga_figure_path": str(reference_assets["reference_paga_figure_path"]),
        "reference_gene_trend_figure_path": str(reference_assets.get("reference_gene_trend_figure_path", "")),
        "reference_summary_path": str(reference_assets["reference_summary_path"]),
        "figure_title": f"{dataset_label} Paper Figure Trajectory" if requires_gene_panel else f"{dataset_label} Trajectory",
        "paga_title": f"{dataset_label} Paper Figure PAGA Graph" if requires_gene_panel else f"{dataset_label} PAGA Graph",
    }
    enable_refinement = bool(benchmark_settings.get("enable_refinement_subgraph", True))
    refinement_threshold = float(benchmark_settings.get("refinement_threshold", 8.0))

    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="BioPlanner",
        description=(
            f"Select the best candidate repo and a concrete paper-style figure-reproduction configuration for the {dataset_label} case study."
            if requires_gene_panel
            else f"Select the best candidate repo and a concrete trajectory-analysis configuration for the {dataset_label} case study."
        ),
        model=model_spec,
        system_prompt=(
            "You are a biological workflow planner. Choose the most appropriate candidate repo for execution, "
            "stay close to the methods excerpt, and output JSON with keys selected_repo, analysis_config, and summary. "
            "selected_repo must be one of the provided candidate repo names."
            + (
                " Preserve the dynamic-gene evidence requirement as part of the target figure package."
                if requires_gene_panel
                else ""
            )
        ),
        io=IOContract(output_schema=_case_refiner_output_schema()),
        max_steps=1,
    )
    tool_node = NodeSpec(
        node_id="scanpy_trajectory_tool",
        kind=NodeKind.tool,
        role="ScanpyTrajectorySandboxAgent",
        description=(
            f"Run the {dataset_label} Scanpy paper-figure reproduction pipeline inside an isolated sandbox via MCP."
            if requires_gene_panel
            else f"Run the {dataset_label} Scanpy trajectory pipeline inside an isolated sandbox via MCP."
        ),
        sandbox=sandbox,
        tools=[ToolRef(server="scanpy_paul15", tool="generate_trajectory")],
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["result"],
                "properties": {
                    "result": {
                        "type": "object",
                        "required": ["status", "summary"],
                        "properties": {
                            "status": {"type": "string"},
                            "summary": {"type": "object"},
                        },
                    }
                },
            },
        ),
    )
    reviewer = NodeSpec(
        node_id="bio_reviewer",
        kind=NodeKind.evaluator,
        role="BioReviewer",
        description=(
            f"Assess methodological faithfulness and biological plausibility of the reproduced {dataset_label} paper-style figure package."
            if requires_gene_panel
            else f"Assess methodological faithfulness and biological plausibility of the reconstructed {dataset_label} trajectory workflow."
        ),
        model=review_model,
        system_prompt=(
            f"You are reviewing a {dataset_label} single-cell trajectory reconstruction. "
            "Use the methods excerpt, target description, and generated summary to score methodology_faithfulness, "
            "biological_plausibility, and artifact_completeness on a 1-10 scale. "
            "The generated outputs should include a trajectory figure, a PAGA graph, structured trajectory statistics, "
            "QC evidence, and marker/dynamic gene evidence. "
            "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, "
            "overall_verdict, concerns, and summary."
            + (
                " Penalize outputs that omit the dynamic-gene figure panel or fail to expose the panel genes clearly."
                if requires_gene_panel
                else ""
            )
        ),
        io=IOContract(output_schema=_case_review_output_schema()),
    )
    subgraphs: list[SubgraphSpec] = []
    gates: list[GateSpec] = []
    if enable_refinement:
        refiner = NodeSpec(
            node_id="case_refiner",
            kind=NodeKind.agent,
            role="CaseRefiner",
            description=(
                f"Revise the {dataset_label} paper-figure reproduction plan after reviewer concerns while staying close to the target methods."
                if requires_gene_panel
                else f"Revise the {dataset_label} trajectory plan after reviewer concerns while staying close to the target methods."
            ),
            model=model_spec,
            system_prompt=(
                f"You are refining a {dataset_label} trajectory case-study plan after a reviewer found issues. "
                "You receive the original selected_repo, analysis_config, tool_result, and review_result. "
                "Revise the analysis_config conservatively to address the concerns while keeping the methods faithful. "
                "Return JSON with keys selected_repo, analysis_config, summary, and rationale. "
                "selected_repo must remain one of the candidate repo names."
                + (
                    " The dynamic-gene evidence panel remains mandatory."
                    if requires_gene_panel
                    else ""
                )
            ),
            io=IOContract(output_schema=_case_refiner_output_schema()),
            max_steps=1,
        )
        refined_tool = tool_node.model_copy(
            deep=True,
            update={
                "node_id": "scanpy_trajectory_tool_refined",
                "description": f"Re-run the {dataset_label} Scanpy trajectory pipeline after reviewer-guided refinement.",
            },
        )
        refined_reviewer = reviewer.model_copy(
            deep=True,
            update={
                "node_id": "bio_reviewer_refined",
                "role": "BioReviewerRefined",
                "description": f"Assess the refined {dataset_label} trajectory reconstruction after a second tool pass.",
            },
        )
        subgraphs.append(
            SubgraphSpec(
                subgraph_id="case_refinement",
                purpose="Reviewer-triggered case-study refinement after low biological or methodological scores.",
                nodes=[refiner, refined_tool, refined_reviewer],
                edges=[
                    EdgeSpec(
                        edge_id="edge_planner_to_case_refiner",
                        src="planner",
                        dst="case_refiner",
                        mapping={
                            "selected_repo": "selected_repo",
                            "analysis_config": "analysis_config",
                            "planner_summary": "summary",
                        },
                    ),
                    EdgeSpec(
                        edge_id="edge_tool_to_case_refiner",
                        src="scanpy_trajectory_tool",
                        dst="case_refiner",
                        mapping={"tool_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_reviewer_to_case_refiner",
                        src="bio_reviewer",
                        dst="case_refiner",
                        mapping={"review_result": "*"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refiner_to_refined_tool",
                        src="case_refiner",
                        dst="scanpy_trajectory_tool_refined",
                        mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refined_tool_to_refined_reviewer",
                        src="scanpy_trajectory_tool_refined",
                        dst="bio_reviewer_refined",
                        mapping={"tool_result": "result"},
                    ),
                ],
                entry_nodes=["case_refiner"],
                exit_nodes=["bio_reviewer_refined"],
                default_enabled=False,
            )
        )
        gates.append(
            GateSpec(
                gate_id="case_refinement_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="bio_reviewer"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(
                                    field="node.outputs.methodology_faithfulness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.biological_plausibility",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.artifact_completeness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["case_refinement"],
                max_activations=1,
            )
        )
    script_root = Path(__file__).resolve().parents[2] / "scripts"
    script_path = script_root / "check_scanpy_paul15_case.py"
    if transfer_case:
        script_path = script_root / "check_scanpy_method_transfer_case.py"
    elif requires_gene_panel:
        script_path = script_root / "check_scanpy_paul15_paper_figure_case.py"
    hard_check_cmd = [
        sys.executable,
        str(script_path),
        "--figure",
        str(figure_path.resolve()),
        "--paga-figure",
        str(paga_figure_path.resolve()),
    ]
    if requires_gene_panel:
        hard_check_cmd.extend(["--gene-trend-figure", str(gene_trend_figure_path.resolve())])
    hard_check_cmd.extend(
        [
            "--summary",
            str(summary_path.resolve()),
            "--reference-summary",
            str(reference_assets["reference_summary_path"]),
        ]
    )
    if transfer_case:
        hard_check_cmd.extend(["--dataset-id", dataset_id])
    return WorkflowBlueprint(
        task=TaskSpec(
            task_id=str(
                benchmark_settings.get(
                    "task_id",
                    f"scanpy_{dataset_id}_transfer_case"
                    if transfer_case
                    else "scanpy_paul15_paper_figure_case"
                    if requires_gene_panel
                    else "scanpy_paul15_case",
                )
            ),
            title=str(
                benchmark_settings.get(
                    "title",
                    f"Scanpy {dataset_label} method transfer"
                    if transfer_case
                    else f"Scanpy {dataset_label} paper-figure reproduction"
                    if requires_gene_panel
                    else f"Scanpy {dataset_label} trajectory reconstruction",
                )
            ),
            description=task_description,
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="scanpy_paul15",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.scanpy_paul15_server"],
                sandbox=sandbox,
            )
        ],
        base_nodes=[planner, tool_node, reviewer],
        base_edges=[
            EdgeSpec(
                edge_id="edge_planner_to_tool",
                src="planner",
                dst="scanpy_trajectory_tool",
                mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
            ),
            EdgeSpec(
                edge_id="edge_tool_to_reviewer",
                src="scanpy_trajectory_tool",
                dst="bio_reviewer",
                mapping={"tool_result": "result"},
            ),
        ],
        subgraphs=subgraphs,
        gates=gates,
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="scanpy_paul15_hard_check",
                    description=(
                        f"Validate the generated {dataset_label} method-transfer figure package against reference artifacts and dataset-specific invariants."
                        if transfer_case
                        else f"Validate the generated {dataset_label} paper-style figure package against the reference summary."
                        if requires_gene_panel
                        else f"Validate generated {dataset_label} trajectory and PAGA figures against the reference summary."
                    ),
                    cmd=hard_check_cmd,
                    timeout_s=120,
                )
            ],
            trace_assertions=[
                {
                    "assertion_id": "tool_invoked",
                    "description": "The sandboxed Scanpy trajectory tool must be invoked at least once.",
                    "expr": "trace.tool_calls >= 1 and 'scanpy_trajectory_tool' in trace.successful_nodes",
                },
                {
                    "assertion_id": "sandbox_materialized",
                    "description": "The sandbox backend should be materialized during the run.",
                    "expr": "trace.container_starts >= 1",
                },
            ],
            soft_judges=[],
        ),
        meta={
            "case_id": case_assets["case_id"],
            "reference_assets": dict(reference_assets),
            "candidate_repo_count": len(candidate_repos),
            "paper_figure_case": requires_gene_panel,
            "dataset_id": dataset_id,
            "dataset_label": dataset_label,
            "transfer_case": transfer_case,
        },
    )


def _build_scanpy_paul15_specialized_collaboration_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
    recorder: RunRecorder,
    paper_spec_sandbox: SandboxSpec,
    planner_sandbox: SandboxSpec,
    trajectory_sandbox: SandboxSpec,
    critic_sandbox: SandboxSpec,
) -> WorkflowBlueprint:
    requires_gene_panel = bool(str(case_assets.get("reference_gene_trend_figure_path", "")).strip())
    dataset_id = str(case_assets.get("dataset_id", "paul15")).strip() or "paul15"
    dataset_label = str(case_assets.get("dataset_label", "Paul15")).strip() or "Paul15"
    transfer_case = dataset_id != "paul15"
    model_spec = ModelSpec.model_validate(config.get("model", {}))
    blueprint_cfg = config.get("blueprint", {})
    budget_payload = dict(blueprint_cfg.get("budget", {})) if isinstance(blueprint_cfg, Mapping) else {}
    budget = BudgetSpec.model_validate(budget_payload or {})
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))

    output_dir = recorder.artifacts_dir / "generated"
    figure_path = output_dir / "generated_trajectory.png"
    paga_figure_path = output_dir / "generated_paga.png"
    gene_trend_figure_path = output_dir / "generated_dynamic_genes.png"
    summary_path = output_dir / "generated_summary.json"
    raw_data_path = recorder.artifacts_dir / f"{dataset_id}_raw.h5ad"
    task_hints = {
        "case_id": case_assets["case_id"],
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(case_assets["methods_excerpt_path"]),
        "target_figure_description": target_description,
        "candidate_repos": candidate_repos,
        "analysis_config": dict(benchmark_settings.get("analysis_config", {})),
        "output_dir": str(output_dir.resolve()),
        "figure_path": str(figure_path.resolve()),
        "paga_figure_path": str(paga_figure_path.resolve()),
        "gene_trend_figure_path": str(gene_trend_figure_path.resolve()) if requires_gene_panel else "",
        "summary_path": str(summary_path.resolve()),
        "raw_data_path": str(raw_data_path.resolve()),
        "reference_figure_path": str(reference_assets["reference_figure_path"]),
        "reference_paga_figure_path": str(reference_assets["reference_paga_figure_path"]),
        "reference_gene_trend_figure_path": str(reference_assets.get("reference_gene_trend_figure_path", "")),
        "reference_summary_path": str(reference_assets["reference_summary_path"]),
        "figure_title": f"{dataset_label} Specialist Collaboration Trajectory",
        "paga_title": f"{dataset_label} Specialist Collaboration PAGA Graph",
    }
    task_description = "\n\n".join(
        [
            (
                f"Use reusable specialized agents to transfer the Paul15-style trajectory method to the {dataset_label} dataset."
                if transfer_case
                else f"Use reusable specialized agents to reproduce the {dataset_label} paper-style trajectory workflow."
            ),
            f"Methods excerpt:\n{methods_excerpt}",
            f"Target figure description:\n{target_description}",
            "Candidate repos:\n" + "\n".join(f"- {item['name']}: {item['url']}" for item in candidate_repos),
        ]
    )
    enable_refinement = bool(benchmark_settings.get("enable_refinement_subgraph", True))
    refinement_threshold = float(benchmark_settings.get("refinement_threshold", 8.0))

    paper_spec = NodeSpec(
        node_id="paper_spec",
        kind=NodeKind.tool,
        role="PaperSpecSpecialist",
        description=f"Interpret the methods excerpt and extract the required artifact contract for the {dataset_label} task.",
        sandbox=paper_spec_sandbox,
        tools=[ToolRef(server="paper_spec_specialist", tool="run_agent")],
        io=IOContract(output_schema=_paper_specialist_output_schema()),
    )
    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.tool,
        role="ExecutionPlannerSpecialist",
        description=f"Select the best repo and conservative analysis config for the {dataset_label} trajectory task.",
        sandbox=planner_sandbox,
        tools=[ToolRef(server="paper_execution_planner", tool="run_agent")],
        io=IOContract(output_schema=_case_refiner_output_schema()),
    )
    tool_node = NodeSpec(
        node_id="scanpy_trajectory_tool",
        kind=NodeKind.tool,
        role="ScanpyTrajectorySpecialist",
        description=f"Execute the sandboxed Scanpy trajectory pipeline for the {dataset_label} task.",
        sandbox=trajectory_sandbox,
        tools=[ToolRef(server="scanpy_paul15", tool="generate_trajectory")],
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["result"],
                "properties": {
                    "result": {
                        "type": "object",
                        "required": ["status", "summary"],
                        "properties": {
                            "status": {"type": "string"},
                            "summary": {"type": "object"},
                        },
                    }
                },
            },
        ),
    )
    reviewer = NodeSpec(
        node_id="bio_reviewer",
        kind=NodeKind.tool,
        role="FigureCriticSpecialist",
        description=f"Critique the generated {dataset_label} artifact package against the methods excerpt and required artifacts.",
        sandbox=critic_sandbox,
        tools=[ToolRef(server="paper_figure_critic", tool="run_agent")],
        io=IOContract(output_schema=_paper_figure_critic_output_schema()),
    )

    subgraphs: list[SubgraphSpec] = []
    gates: list[GateSpec] = []
    if enable_refinement:
        refiner = NodeSpec(
            node_id="case_refiner",
            kind=NodeKind.agent,
            role="CaseRefiner",
            description=f"Revise the {dataset_label} trajectory analysis config after specialist critic concerns.",
            model=model_spec,
            system_prompt=(
                f"You are refining a {dataset_label} trajectory plan after a specialist critic found issues. "
                "Revise the analysis_config conservatively while preserving faithfulness to the provided methods excerpt. "
                "Return JSON with keys selected_repo, analysis_config, summary, and rationale."
            ),
            io=IOContract(output_schema=_case_refiner_output_schema()),
            max_steps=1,
        )
        refined_tool = tool_node.model_copy(
            deep=True,
            update={
                "node_id": "scanpy_trajectory_tool_refined",
                "description": f"Re-run the sandboxed {dataset_label} trajectory pipeline after refinement.",
            },
        )
        refined_reviewer = reviewer.model_copy(
            deep=True,
            update={
                "node_id": "bio_reviewer_refined",
                "role": "FigureCriticSpecialistRefined",
                "description": f"Re-assess the refined {dataset_label} artifact package.",
            },
        )
        subgraphs.append(
            SubgraphSpec(
                subgraph_id="case_refinement",
                purpose="Specialist-critic-triggered refinement for low-scoring artifact packages.",
                nodes=[refiner, refined_tool, refined_reviewer],
                edges=[
                    EdgeSpec(
                        edge_id="edge_planner_to_case_refiner",
                        src="planner",
                        dst="case_refiner",
                        mapping={
                            "selected_repo": "selected_repo",
                            "analysis_config": "analysis_config",
                            "planner_summary": "summary",
                        },
                    ),
                    EdgeSpec(
                        edge_id="edge_tool_to_case_refiner",
                        src="scanpy_trajectory_tool",
                        dst="case_refiner",
                        mapping={"tool_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_reviewer_to_case_refiner",
                        src="bio_reviewer",
                        dst="case_refiner",
                        mapping={"review_result": "*"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refiner_to_refined_tool",
                        src="case_refiner",
                        dst="scanpy_trajectory_tool_refined",
                        mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
                    ),
                    EdgeSpec(
                        edge_id="edge_paper_spec_to_refined_reviewer",
                        src="paper_spec",
                        dst="bio_reviewer_refined",
                        mapping={"paper_spec": "*"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refined_tool_to_refined_reviewer",
                        src="scanpy_trajectory_tool_refined",
                        dst="bio_reviewer_refined",
                        mapping={"tool_result": "result"},
                    ),
                ],
                entry_nodes=["case_refiner"],
                exit_nodes=["bio_reviewer_refined"],
                default_enabled=False,
            )
        )
        gates.append(
            GateSpec(
                gate_id="case_refinement_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="bio_reviewer"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(
                                    field="node.outputs.methodology_faithfulness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.biological_plausibility",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.artifact_completeness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["case_refinement"],
                max_activations=1,
            )
        )

    script_root = Path(__file__).resolve().parents[2] / "scripts"
    script_path = script_root / "check_scanpy_method_transfer_case.py" if transfer_case else script_root / "check_scanpy_paul15_paper_figure_case.py"
    hard_check_cmd = [
        sys.executable,
        str(script_path),
        "--figure",
        str(figure_path.resolve()),
        "--paga-figure",
        str(paga_figure_path.resolve()),
        "--gene-trend-figure",
        str(gene_trend_figure_path.resolve()),
        "--summary",
        str(summary_path.resolve()),
        "--reference-summary",
        str(reference_assets["reference_summary_path"]),
    ]
    if transfer_case:
        hard_check_cmd.extend(["--dataset-id", dataset_id])

    return WorkflowBlueprint(
        task=TaskSpec(
            task_id=str(
                benchmark_settings.get(
                    "task_id",
                    f"scanpy_{dataset_id}_specialized_collaboration_case"
                    if transfer_case
                    else "scanpy_paul15_specialized_collaboration_case",
                )
            ),
            title=str(
                benchmark_settings.get(
                    "title",
                    f"Scanpy {dataset_label} specialized-agent collaboration",
                )
            ),
            description=task_description,
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="paper_spec_specialist",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.paper_figure_specialist_server"],
                sandbox=paper_spec_sandbox,
            ),
            MCPServerRef(
                name="paper_execution_planner",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.paper_figure_specialist_server"],
                sandbox=planner_sandbox,
            ),
            MCPServerRef(
                name="scanpy_paul15",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.scanpy_paul15_server"],
                sandbox=trajectory_sandbox,
            ),
            MCPServerRef(
                name="paper_figure_critic",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.paper_figure_specialist_server"],
                sandbox=critic_sandbox,
            ),
        ],
        base_nodes=[paper_spec, planner, tool_node, reviewer],
        base_edges=[
            EdgeSpec(
                edge_id="edge_paper_spec_to_planner",
                src="paper_spec",
                dst="planner",
                mapping={"paper_spec": "*"},
            ),
            EdgeSpec(
                edge_id="edge_planner_to_tool",
                src="planner",
                dst="scanpy_trajectory_tool",
                mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
            ),
            EdgeSpec(
                edge_id="edge_paper_spec_to_reviewer",
                src="paper_spec",
                dst="bio_reviewer",
                mapping={"paper_spec": "*"},
            ),
            EdgeSpec(
                edge_id="edge_tool_to_reviewer",
                src="scanpy_trajectory_tool",
                dst="bio_reviewer",
                mapping={"tool_result": "result"},
            ),
        ],
        subgraphs=subgraphs,
        gates=gates,
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="scanpy_specialist_collaboration_hard_check",
                    description=(
                        f"Validate the generated {dataset_label} transfer artifact package against dataset-specific invariants."
                        if transfer_case
                        else f"Validate the generated {dataset_label} specialist-collaboration artifact package against the reference summary."
                    ),
                    cmd=hard_check_cmd,
                    timeout_s=120,
                )
            ],
            trace_assertions=[
                {
                    "assertion_id": "trajectory_tool_invoked",
                    "description": "The sandboxed Scanpy trajectory specialist must be invoked at least once.",
                    "expr": "trace.tool_calls >= 2 and 'scanpy_trajectory_tool' in trace.successful_nodes",
                },
                {
                    "assertion_id": "specialists_invoked",
                    "description": "The paper-spec specialist, execution planner, and figure critic should all run.",
                    "expr": "'paper_spec' in trace.successful_nodes and 'planner' in trace.successful_nodes and 'bio_reviewer' in trace.successful_nodes",
                },
                {
                    "assertion_id": "sandbox_materialized",
                    "description": "The collaboration run should materialize at least three sandbox starts.",
                    "expr": "trace.container_starts >= 3",
                },
            ],
            soft_judges=[],
        ),
        meta={
            "case_id": case_assets["case_id"],
            "reference_assets": dict(reference_assets),
            "candidate_repo_count": len(candidate_repos),
            "paper_figure_case": True,
            "dataset_id": dataset_id,
            "dataset_label": dataset_label,
            "transfer_case": transfer_case,
            "collaboration_case": True,
        },
    )


def _build_squidpy_case_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
    recorder: RunRecorder,
    sandbox: SandboxSpec,
) -> WorkflowBlueprint:
    model_spec = ModelSpec.model_validate(config.get("model", {}))
    review_payload = config.get("review_model", benchmark_settings.get("review_model", config.get("model", {})))
    review_model = ModelSpec.model_validate(review_payload)
    blueprint_cfg = config.get("blueprint", {})
    budget_payload = dict(blueprint_cfg.get("budget", {})) if isinstance(blueprint_cfg, Mapping) else {}
    budget = BudgetSpec.model_validate(budget_payload or {})
    case_id = str(case_assets.get("case_id", "squidpy_visium_hne_spatial"))
    dataset_id = str(case_assets.get("dataset_id", "visium_hne_adata_crop")).strip() or "visium_hne_adata_crop"
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    interaction_case = case_id in {"squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}
    transfer_case = case_id == "squidpy_seqfish_method_transfer"
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))

    output_dir = recorder.artifacts_dir / "generated"
    figure_path = output_dir / "generated_spatial.png"
    interaction_figure_path = output_dir / "generated_nhood_enrichment.png"
    summary_path = output_dir / "generated_summary.json"
    raw_data_path = recorder.artifacts_dir / ("seqfish.h5ad" if dataset_id == "seqfish" else "visium_hne_adata_crop.h5ad")
    task_description = "\n\n".join(
        [
            (
                f"Transfer the target {dataset_label} spatial-interaction workflow as faithfully as possible using one of the candidate repos."
                if transfer_case
                else f"Reproduce the target {dataset_label} spatial plot and interaction analysis as faithfully as possible using one of the candidate repos."
                if interaction_case
                else f"Reproduce the target {dataset_label} spatial plot as faithfully as possible using one of the candidate repos."
            ),
            f"Methods excerpt:\n{methods_excerpt}",
            f"Target figure description:\n{target_description}",
            "Candidate repos:\n" + "\n".join(f"- {item['name']}: {item['url']}" for item in candidate_repos),
        ]
    )
    task_hints = {
        "case_id": case_assets["case_id"],
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(case_assets["methods_excerpt_path"]),
        "target_figure_description": target_description,
        "candidate_repos": candidate_repos,
        "analysis_config": dict(benchmark_settings.get("analysis_config", {})),
        "planner_mode": str(benchmark_settings.get("planner_mode", "llm_first")),
        "output_dir": str(output_dir.resolve()),
        "figure_path": str(figure_path.resolve()),
        "interaction_figure_path": str(interaction_figure_path.resolve()) if interaction_case else "",
        "summary_path": str(summary_path.resolve()),
        "raw_data_path": str(raw_data_path.resolve()),
        "reference_figure_path": str(reference_assets["reference_figure_path"]),
        "reference_interaction_figure_path": str(reference_assets.get("reference_interaction_figure_path", "")),
        "reference_summary_path": str(reference_assets["reference_summary_path"]),
        "figure_title": f"{dataset_label} Neighborhood Enrichment" if interaction_case else f"{dataset_label} Spatial Plot",
    }
    enable_refinement = bool(benchmark_settings.get("enable_refinement_subgraph", True))
    refinement_threshold = float(benchmark_settings.get("refinement_threshold", 8.0))

    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="SpatialBioPlanner",
        description=(
            f"Select the best candidate repo and a concrete Squidpy interaction-analysis configuration for the {dataset_label} spatial task."
            if interaction_case
            else f"Select the best candidate repo and a concrete Squidpy visualization configuration for the {dataset_label} spatial task."
        ),
        model=model_spec,
        system_prompt=(
            "You are a spatial-omics workflow planner. Choose the most appropriate candidate repo for execution, "
            "stay close to the methods excerpt, and output JSON with keys selected_repo, analysis_config, and summary. "
            "selected_repo must be one of the provided candidate repo names."
            + (
                " Prefer plans that preserve the canonical Squidpy interaction-analysis path: spatial neighbors, neighborhood enrichment, and faithful output artifacts."
                if interaction_case
                else ""
            )
        ),
        io=IOContract(output_schema=_case_refiner_output_schema()),
        max_steps=1,
    )
    tool_node = NodeSpec(
        node_id="squidpy_tool",
        kind=NodeKind.tool,
        role="SquidpySandboxAgent",
        description=(
            f"Run the {dataset_label} spatial-interaction pipeline inside an isolated sandbox via MCP."
            if interaction_case
            else f"Run the {dataset_label} spatial-plot pipeline inside an isolated sandbox via MCP."
        ),
        sandbox=sandbox,
        tools=[
            ToolRef(
                server="squidpy_visium",
                tool="generate_interaction_report" if interaction_case else "generate_spatial_plot",
            )
        ],
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["result"],
                "properties": {
                    "result": {
                        "type": "object",
                        "required": ["status", "summary"],
                        "properties": {
                            "status": {"type": "string"},
                            "summary": {"type": "object"},
                        },
                    }
                },
            },
        ),
    )
    reviewer = NodeSpec(
        node_id="bio_reviewer",
        kind=NodeKind.evaluator,
        role="SpatialBioReviewer",
        description=(
            f"Assess methodological faithfulness and biological plausibility of the reproduced {dataset_label} spatial-interaction analysis."
            if interaction_case
            else f"Assess methodological faithfulness and biological plausibility of the reproduced {dataset_label} spatial figure."
        ),
        model=review_model,
        system_prompt=(
            "You are reviewing a spatial-transcriptomics case-study output. "
            "Use the methods excerpt, target description, and generated summary to score methodology_faithfulness, "
            "biological_plausibility, and artifact_completeness on a 1-10 scale. "
            "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, "
            "overall_verdict, concerns, and summary."
        ),
        io=IOContract(output_schema=_case_review_output_schema()),
    )
    subgraphs: list[SubgraphSpec] = []
    gates: list[GateSpec] = []
    if enable_refinement:
        refiner = NodeSpec(
            node_id="case_refiner",
            kind=NodeKind.agent,
            role="SpatialCaseRefiner",
            description=(
                "Revise the spatial interaction-analysis plan after reviewer concerns while staying close to the target methods."
                if interaction_case
                else "Revise the spatial analysis plan after reviewer concerns while staying close to the target methods."
            ),
            model=model_spec,
            system_prompt=(
                f"You are refining a {dataset_label} spatial-analysis plan after a reviewer found issues. "
                "You receive the original selected_repo, analysis_config, tool_result, and review_result. "
                "Revise the analysis_config conservatively to address the concerns while keeping the methods faithful. "
                "Return JSON with keys selected_repo, analysis_config, summary, and rationale. "
                "selected_repo must remain one of the candidate repo names."
            ),
            io=IOContract(output_schema=_case_refiner_output_schema()),
            max_steps=1,
        )
        refined_tool = tool_node.model_copy(
            deep=True,
            update={
                "node_id": "squidpy_tool_refined",
                "description": (
                    "Re-run the Visium spatial-interaction pipeline after reviewer-guided refinement."
                    if interaction_case
                    else "Re-run the Visium spatial pipeline after reviewer-guided refinement."
                ),
            },
        )
        refined_reviewer = reviewer.model_copy(
            deep=True,
            update={
                "node_id": "bio_reviewer_refined",
                "role": "SpatialBioReviewerRefined",
                "description": (
                    "Assess the refined Visium spatial-interaction analysis after a second tool pass."
                    if interaction_case
                    else "Assess the refined Visium spatial reproduction after a second tool pass."
                ),
            },
        )
        subgraphs.append(
            SubgraphSpec(
                subgraph_id="case_refinement",
                purpose="Reviewer-triggered case-study refinement after low biological or methodological scores.",
                nodes=[refiner, refined_tool, refined_reviewer],
                edges=[
                    EdgeSpec(
                        edge_id="edge_planner_to_case_refiner",
                        src="planner",
                        dst="case_refiner",
                        mapping={
                            "selected_repo": "selected_repo",
                            "analysis_config": "analysis_config",
                            "planner_summary": "summary",
                        },
                    ),
                    EdgeSpec(
                        edge_id="edge_tool_to_case_refiner",
                        src="squidpy_tool",
                        dst="case_refiner",
                        mapping={"tool_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_reviewer_to_case_refiner",
                        src="bio_reviewer",
                        dst="case_refiner",
                        mapping={"review_result": "*"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refiner_to_refined_tool",
                        src="case_refiner",
                        dst="squidpy_tool_refined",
                        mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
                    ),
                    EdgeSpec(
                        edge_id="edge_refined_tool_to_refined_reviewer",
                        src="squidpy_tool_refined",
                        dst="bio_reviewer_refined",
                        mapping={"tool_result": "result"},
                    ),
                ],
                entry_nodes=["case_refiner"],
                exit_nodes=["bio_reviewer_refined"],
                default_enabled=False,
            )
        )
        gates.append(
            GateSpec(
                gate_id="case_refinement_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="bio_reviewer"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(
                                    field="node.outputs.methodology_faithfulness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.biological_plausibility",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                                AtomicCondition(
                                    field="node.outputs.artifact_completeness",
                                    op=ConditionOp.lt,
                                    value=refinement_threshold,
                                ),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["case_refinement"],
                max_activations=1,
            )
        )
    script_name = "check_squidpy_visium_interactions_case.py" if interaction_case else "check_squidpy_visium_case.py"
    script_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
    hard_check_cmd = [
        sys.executable,
        str(script_path),
        "--figure",
        str(figure_path.resolve()),
    ]
    if interaction_case:
        hard_check_cmd.extend(["--interaction-figure", str(interaction_figure_path.resolve())])
    hard_check_cmd.extend(
        [
            "--summary",
            str(summary_path.resolve()),
            "--reference-summary",
            str(reference_assets["reference_summary_path"]),
            "--dataset-id",
            dataset_id,
            "--expect-spatial-image",
            "false" if dataset_id == "seqfish" else "true",
        ]
    )
    expected_cluster_key = str(task_hints["analysis_config"].get("cluster_key", "")).strip()
    if expected_cluster_key:
        hard_check_cmd.extend(["--expected-cluster-key", expected_cluster_key])
    return WorkflowBlueprint(
        task=TaskSpec(
            task_id=str(
                benchmark_settings.get(
                    "task_id",
                    "squidpy_seqfish_transfer_case"
                    if transfer_case
                    else "squidpy_visium_interactions_case"
                    if interaction_case
                    else "squidpy_visium_case",
                )
            ),
            title=str(
                benchmark_settings.get(
                    "title",
                    f"Squidpy {dataset_label} interaction analysis"
                    if interaction_case
                    else f"Squidpy {dataset_label} spatial plot reproduction",
                )
            ),
            description=task_description,
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="squidpy_visium",
                transport="stdio",
                stdio_cmd=[
                    "python",
                    "-m",
                    "agentcoop.case_studies.squidpy_visium_interactions_server"
                    if interaction_case
                    else "agentcoop.case_studies.squidpy_visium_server",
                ],
                sandbox=sandbox,
            )
        ],
        base_nodes=[planner, tool_node, reviewer],
        base_edges=[
            EdgeSpec(
                edge_id="edge_planner_to_tool",
                src="planner",
                dst="squidpy_tool",
                mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
            ),
            EdgeSpec(
                edge_id="edge_tool_to_reviewer",
                src="squidpy_tool",
                dst="bio_reviewer",
                mapping={"tool_result": "result"},
            ),
        ],
        subgraphs=subgraphs,
        gates=gates,
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="squidpy_visium_hard_check",
                    description=(
                        f"Validate generated {dataset_label} spatial figure, interaction artifact, and summary against the reference summary."
                        if interaction_case
                        else f"Validate generated {dataset_label} spatial figure and summary against the reference summary."
                    ),
                    cmd=hard_check_cmd,
                    timeout_s=120,
                )
            ],
            trace_assertions=[
                {
                    "assertion_id": "tool_invoked",
                    "description": "The sandboxed Squidpy tool must be invoked at least once.",
                    "expr": "trace.tool_calls >= 1 and 'squidpy_tool' in trace.successful_nodes",
                },
                {
                    "assertion_id": "sandbox_materialized",
                    "description": "The sandbox backend should be materialized during the run.",
                    "expr": "trace.container_starts >= 1",
                },
            ],
            soft_judges=[],
        ),
        meta={
            "case_id": case_assets["case_id"],
            "reference_assets": dict(reference_assets),
            "candidate_repo_count": len(candidate_repos),
        },
    )


def _build_visium_multi_agent_collaboration_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
    recorder: RunRecorder,
    scanpy_sandbox: SandboxSpec,
    squidpy_sandbox: SandboxSpec,
) -> WorkflowBlueprint:
    model_spec = ModelSpec.model_validate(config.get("model", {}))
    review_payload = config.get("review_model", benchmark_settings.get("review_model", config.get("model", {})))
    review_model = ModelSpec.model_validate(review_payload)
    blueprint_cfg = config.get("blueprint", {})
    budget_payload = dict(blueprint_cfg.get("budget", {})) if isinstance(blueprint_cfg, Mapping) else {}
    budget = BudgetSpec.model_validate(budget_payload or {})
    dataset_id = str(case_assets.get("dataset_id", "visium_hne_adata_crop")).strip() or "visium_hne_adata_crop"
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))
    scanpy_candidates = [repo for repo in candidate_repos if str(repo.get("name", "")).startswith("scanpy")]
    squidpy_candidates = [repo for repo in candidate_repos if str(repo.get("name", "")) == "squidpy"]

    output_dir = recorder.artifacts_dir / "generated"
    cluster_figure_path = output_dir / "generated_cluster_umap.png"
    marker_figure_path = output_dir / "generated_marker_heatmap.png"
    cluster_summary_path = output_dir / "generated_cluster_summary.json"
    annotated_data_path = output_dir / "generated_annotated_visium.h5ad"
    spatial_figure_path = output_dir / "generated_spatial.png"
    interaction_figure_path = output_dir / "generated_nhood_enrichment.png"
    interaction_summary_path = output_dir / "generated_interaction_summary.json"
    summary_path = output_dir / "generated_summary.json"
    # Collaboration specialists should reuse the prepared benchmark input rather than
    # trigger their own dataset-download fallback from an empty run-local path.
    raw_data_path = Path(str(reference_assets["raw_data_path"])).resolve()

    task_hints = {
        "case_id": case_assets["case_id"],
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "scanpy_candidate_repos": scanpy_candidates,
        "squidpy_candidate_repos": squidpy_candidates,
        "cluster_analysis_config": dict(benchmark_settings.get("cluster_analysis_config", {})),
        "interaction_analysis_config": dict(benchmark_settings.get("interaction_analysis_config", {})),
        "planner_mode": str(benchmark_settings.get("planner_mode", "fixed")),
        "output_dir": str(output_dir.resolve()),
        "cluster_figure_path": str(cluster_figure_path.resolve()),
        "marker_figure_path": str(marker_figure_path.resolve()),
        "cluster_summary_path": str(cluster_summary_path.resolve()),
        "annotated_data_path": str(annotated_data_path.resolve()),
        "spatial_figure_path": str(spatial_figure_path.resolve()),
        "interaction_figure_path": str(interaction_figure_path.resolve()),
        "interaction_summary_path": str(interaction_summary_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "raw_data_path": str(raw_data_path.resolve()),
        "cluster_title": f"{dataset_label} Scanpy Clusters",
        "marker_title": f"{dataset_label} Marker Heatmap",
        "spatial_title": f"{dataset_label} Spatial Layout",
        "interaction_title": f"{dataset_label} Neighborhood Enrichment",
        "reference_summary_path": str(reference_assets["reference_summary_path"]),
    }
    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="MultiAgentPlanner",
        description=f"Select the specialized-agent collaboration plan for {dataset_label} clustering and spatial interaction analysis.",
        model=model_spec,
        system_prompt=(
            f"You are planning a multi-specialized-agent {dataset_label} spatial-transcriptomics workflow. "
            "Return JSON with keys scanpy_repo, cluster_analysis_config, squidpy_repo, interaction_analysis_config, and summary. "
            "Use a Scanpy specialist for clustering/marker discovery and a Squidpy specialist for spatial interactions."
        ),
        io=IOContract(output_schema=_visium_multi_agent_plan_output_schema()),
        max_steps=1,
    )
    scanpy_tool = NodeSpec(
        node_id="scanpy_cluster_tool",
        kind=NodeKind.tool,
        role="ScanpyVisiumClusterAgent",
        description=f"Run the {dataset_label} Scanpy clustering specialist in an isolated sandbox.",
        sandbox=scanpy_sandbox,
        tools=[ToolRef(server="scanpy_visium_cluster", tool="generate_clusters")],
        io=IOContract(output_schema={"type": "object", "required": ["result"], "properties": {"result": {"type": "object"}}}),
        meta={"direct_sandbox_handler": True},
    )
    squidpy_tool = NodeSpec(
        node_id="squidpy_interaction_tool",
        kind=NodeKind.tool,
        role="SquidpyVisiumInteractionAgent",
        description=f"Run the {dataset_label} Squidpy interaction specialist in an isolated sandbox using the learned cluster labels.",
        sandbox=squidpy_sandbox,
        tools=[ToolRef(server="squidpy_visium", tool="generate_interaction_report")],
        io=IOContract(output_schema={"type": "object", "required": ["result"], "properties": {"result": {"type": "object"}}}),
        meta={"direct_sandbox_handler": True},
    )
    reviewer = NodeSpec(
        node_id="bio_reviewer",
        kind=NodeKind.evaluator,
        role="SpatialBioReviewer",
        description=f"Assess whether the collaboration between Scanpy and Squidpy specialists produced a coherent {dataset_label} analysis package.",
        model=review_model,
        system_prompt=(
            "You are reviewing a multi-specialized-agent spatial-transcriptomics workflow. "
            "Use the clustering summary and the spatial interaction summary to score methodology_faithfulness, "
            "biological_plausibility, and artifact_completeness on a 1-10 scale. "
            "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, "
            "overall_verdict, concerns, and summary."
        ),
        io=IOContract(output_schema=_case_review_output_schema()),
    )
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_visium_multi_agent_case.py"
    return WorkflowBlueprint(
        task=TaskSpec(
            task_id="seqfish_multi_agent_collaboration_case" if dataset_id == "seqfish" else "visium_multi_agent_collaboration_case",
            title=str(benchmark_settings.get("title", f"{dataset_label} multi-specialized-agent collaboration")),
            description="\n\n".join(
                [
                    f"Run a two-specialist collaboration on public {dataset_label} data: Scanpy for clustering/markers, then Squidpy for spatial interactions.",
                    f"Methods excerpt:\n{methods_excerpt}",
                    f"Target figure description:\n{target_description}",
                ]
            ),
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="scanpy_visium_cluster",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.scanpy_visium_cluster_server"],
                sandbox=scanpy_sandbox,
            ),
            MCPServerRef(
                name="squidpy_visium",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.squidpy_visium_interactions_server"],
                sandbox=squidpy_sandbox,
            ),
        ],
        base_nodes=[planner, scanpy_tool, squidpy_tool, reviewer],
        base_edges=[
            EdgeSpec(
                edge_id="edge_planner_to_scanpy_cluster",
                src="planner",
                dst="scanpy_cluster_tool",
                mapping={"scanpy_repo": "scanpy_repo", "cluster_analysis_config": "cluster_analysis_config"},
            ),
            EdgeSpec(
                edge_id="edge_planner_to_squidpy_interaction",
                src="planner",
                dst="squidpy_interaction_tool",
                mapping={"squidpy_repo": "squidpy_repo", "interaction_analysis_config": "interaction_analysis_config"},
            ),
            EdgeSpec(
                edge_id="edge_scanpy_to_squidpy",
                src="scanpy_cluster_tool",
                dst="squidpy_interaction_tool",
                mapping={
                    "input_h5ad_path": "result.summary.annotated_data_path",
                    "cluster_key": "result.summary.cluster_key",
                },
            ),
            EdgeSpec(
                edge_id="edge_scanpy_to_reviewer",
                src="scanpy_cluster_tool",
                dst="bio_reviewer",
                mapping={"cluster_result": "result"},
            ),
            EdgeSpec(
                edge_id="edge_squidpy_to_reviewer",
                src="squidpy_interaction_tool",
                dst="bio_reviewer",
                mapping={"interaction_result": "result"},
            ),
        ],
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="visium_multi_agent_hard_check",
                    description="Validate the collaboration outputs against the monolith-generated reference summary.",
                    cmd=[
                        sys.executable,
                        str(script_path),
                        "--cluster-figure",
                        str(cluster_figure_path.resolve()),
                        "--marker-figure",
                        str(marker_figure_path.resolve()),
                        "--spatial-figure",
                        str(spatial_figure_path.resolve()),
                        "--interaction-figure",
                        str(interaction_figure_path.resolve()),
                        "--cluster-summary",
                        str(cluster_summary_path.resolve()),
                        "--interaction-summary",
                        str(interaction_summary_path.resolve()),
                        "--reference-summary",
                        str(reference_assets["reference_summary_path"]),
                        "--dataset-id",
                        dataset_id,
                    ],
                    timeout_s=180,
                )
            ],
            trace_assertions=[
                {
                    "assertion_id": "both_specialists_invoked",
                    "description": "Both Scanpy and Squidpy specialists should run.",
                    "expr": "'scanpy_cluster_tool' in trace.successful_nodes and 'squidpy_interaction_tool' in trace.successful_nodes",
                },
                {
                    "assertion_id": "two_sandboxes_materialized",
                    "description": "The collaboration case should materialize two sandboxed specialists.",
                    "expr": "trace.container_starts >= 2",
                },
            ],
            soft_judges=[],
        ),
        meta={
            "case_id": case_assets["case_id"],
            "dataset_id": dataset_id,
            "dataset_label": dataset_label,
            "reference_assets": dict(reference_assets),
            "collaboration_case": True,
            "execution_mode": "direct_sandbox_only",
        },
    )


def _build_visium_multi_agent_monolith_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
    recorder: RunRecorder,
    sandbox: SandboxSpec,
) -> WorkflowBlueprint:
    model_spec = ModelSpec.model_validate(config.get("model", {}))
    review_payload = config.get("review_model", benchmark_settings.get("review_model", config.get("model", {})))
    review_model = ModelSpec.model_validate(review_payload)
    blueprint_cfg = config.get("blueprint", {})
    budget_payload = dict(blueprint_cfg.get("budget", {})) if isinstance(blueprint_cfg, Mapping) else {}
    budget = BudgetSpec.model_validate(budget_payload or {})
    dataset_id = str(case_assets.get("dataset_id", "visium_hne_adata_crop")).strip() or "visium_hne_adata_crop"
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))
    scanpy_candidates = [repo for repo in candidate_repos if str(repo.get("name", "")).startswith("scanpy")]
    squidpy_candidates = [repo for repo in candidate_repos if str(repo.get("name", "")) == "squidpy"]

    output_dir = recorder.artifacts_dir / "generated"
    cluster_figure_path = output_dir / "generated_cluster_umap.png"
    marker_figure_path = output_dir / "generated_marker_heatmap.png"
    cluster_summary_path = output_dir / "generated_cluster_summary.json"
    annotated_data_path = output_dir / "generated_annotated_visium.h5ad"
    spatial_figure_path = output_dir / "generated_spatial.png"
    interaction_figure_path = output_dir / "generated_nhood_enrichment.png"
    interaction_summary_path = output_dir / "generated_interaction_summary.json"
    summary_path = output_dir / "generated_summary.json"
    raw_data_path = recorder.artifacts_dir / ("seqfish_raw.h5ad" if dataset_id == "seqfish" else "visium_hne_raw.h5ad")

    task_hints = {
        "case_id": case_assets["case_id"],
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "scanpy_candidate_repos": scanpy_candidates,
        "squidpy_candidate_repos": squidpy_candidates,
        "cluster_analysis_config": dict(benchmark_settings.get("cluster_analysis_config", {})),
        "interaction_analysis_config": dict(benchmark_settings.get("interaction_analysis_config", {})),
        "planner_mode": str(benchmark_settings.get("planner_mode", "fixed")),
        "output_dir": str(output_dir.resolve()),
        "cluster_figure_path": str(cluster_figure_path.resolve()),
        "marker_figure_path": str(marker_figure_path.resolve()),
        "cluster_summary_path": str(cluster_summary_path.resolve()),
        "annotated_data_path": str(annotated_data_path.resolve()),
        "spatial_figure_path": str(spatial_figure_path.resolve()),
        "interaction_figure_path": str(interaction_figure_path.resolve()),
        "interaction_summary_path": str(interaction_summary_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "raw_data_path": str(raw_data_path.resolve()),
        "cluster_title": f"{dataset_label} Scanpy Clusters",
        "marker_title": f"{dataset_label} Marker Heatmap",
        "spatial_title": f"{dataset_label} Spatial Layout",
        "interaction_title": f"{dataset_label} Neighborhood Enrichment",
        "reference_summary_path": str(reference_assets["reference_summary_path"]),
    }
    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="MultiAgentPlanner",
        description=f"Select the monolith baseline configuration for the {dataset_label} collaboration task.",
        model=model_spec,
        system_prompt=(
            f"You are planning a monolith baseline for a {dataset_label} analysis task. "
            "Return JSON with keys scanpy_repo, cluster_analysis_config, squidpy_repo, interaction_analysis_config, and summary."
        ),
        io=IOContract(output_schema=_visium_multi_agent_plan_output_schema()),
        max_steps=1,
    )
    tool_node = NodeSpec(
        node_id="visium_monolith_tool",
        kind=NodeKind.tool,
        role="VisiumMonolithAgent",
        description=f"Run a monolithic Scanpy+Squidpy baseline in one sandbox for the {dataset_label} task.",
        sandbox=sandbox,
        tools=[ToolRef(server="visium_multi_agent_monolith", tool="generate_joint_report")],
        io=IOContract(output_schema={"type": "object", "required": ["result"], "properties": {"result": {"type": "object"}}}),
        meta={"direct_sandbox_handler": True},
    )
    reviewer = NodeSpec(
        node_id="bio_reviewer",
        kind=NodeKind.evaluator,
        role="SpatialBioReviewer",
        description=f"Assess the monolithic baseline output for the {dataset_label} collaboration task.",
        model=review_model,
        system_prompt=(
            "You are reviewing a monolithic spatial-transcriptomics analysis output. "
            "Use the generated summary to score methodology_faithfulness, biological_plausibility, and artifact_completeness on a 1-10 scale. "
            "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, overall_verdict, concerns, and summary."
        ),
        io=IOContract(output_schema=_case_review_output_schema()),
    )
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_visium_multi_agent_case.py"
    return WorkflowBlueprint(
        task=TaskSpec(
            task_id="seqfish_multi_agent_monolith_case" if dataset_id == "seqfish" else "visium_multi_agent_monolith_case",
            title=str(benchmark_settings.get("title", f"{dataset_label} multi-agent monolith baseline")),
            description="\n\n".join(
                [
                    f"Run a monolithic baseline for the {dataset_label} collaboration task.",
                    f"Methods excerpt:\n{methods_excerpt}",
                    f"Target figure description:\n{target_description}",
                ]
            ),
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="visium_multi_agent_monolith",
                transport="stdio",
                stdio_cmd=["python", "-m", "agentcoop.case_studies.visium_multi_agent_monolith_server"],
                sandbox=sandbox,
            )
        ],
        base_nodes=[planner, tool_node, reviewer],
        base_edges=[
            EdgeSpec(
                edge_id="edge_planner_to_monolith",
                src="planner",
                dst="visium_monolith_tool",
                mapping={
                    "scanpy_repo": "scanpy_repo",
                    "cluster_analysis_config": "cluster_analysis_config",
                    "squidpy_repo": "squidpy_repo",
                    "interaction_analysis_config": "interaction_analysis_config",
                },
            ),
            EdgeSpec(
                edge_id="edge_monolith_to_reviewer",
                src="visium_monolith_tool",
                dst="bio_reviewer",
                mapping={"tool_result": "result"},
            ),
        ],
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="visium_multi_agent_monolith_hard_check",
                    description="Validate the monolith baseline outputs against the reference summary.",
                    cmd=[
                        sys.executable,
                        str(script_path),
                        "--cluster-figure",
                        str(cluster_figure_path.resolve()),
                        "--marker-figure",
                        str(marker_figure_path.resolve()),
                        "--spatial-figure",
                        str(spatial_figure_path.resolve()),
                        "--interaction-figure",
                        str(interaction_figure_path.resolve()),
                        "--cluster-summary",
                        str(cluster_summary_path.resolve()),
                        "--interaction-summary",
                        str(interaction_summary_path.resolve()),
                        "--reference-summary",
                        str(reference_assets["reference_summary_path"]),
                        "--dataset-id",
                        dataset_id,
                    ],
                    timeout_s=180,
                )
            ],
            trace_assertions=[
                {
                    "assertion_id": "monolith_tool_invoked",
                    "description": "The monolith baseline tool should run once.",
                    "expr": "'visium_monolith_tool' in trace.successful_nodes",
                }
            ],
            soft_judges=[],
        ),
        meta={
            "case_id": case_assets["case_id"],
            "dataset_id": dataset_id,
            "dataset_label": dataset_label,
            "reference_assets": dict(reference_assets),
            "monolith_case": True,
            "execution_mode": "direct_sandbox_only",
        },
    )


def _sandbox_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    try:
        tool_result = context.mcp_client.call_bound_tool(
            node.tools,
            f"{node.tools[0].server}:{node.tools[0].tool}",
            dict(inputs),
            blueprint=context.blueprint,
            sandbox_runner=context.sandbox_runner,
        )
    except Exception as exc:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"MCP tool call failed: {exc}",
            trace={"live_mcp": True, "tool": f"{node.tools[0].server}:{node.tools[0].tool}"},
        )

    payload = tool_result if isinstance(tool_result, Mapping) else {"raw_result": tool_result}
    if isinstance(payload, Mapping):
        structured = payload.get("structured")
        if isinstance(structured, Mapping):
            if isinstance(structured.get("result"), Mapping):
                payload = dict(structured["result"])
            else:
                payload = dict(structured)
    summary = payload.get("summary", {}) if isinstance(payload, Mapping) else {}
    cluster_count = int(summary.get("cluster_count", 0) or 0) if isinstance(summary, Mapping) else 0
    confidence = min(0.98, 0.55 + 0.05 * min(cluster_count, 8))
    artifacts = []
    if isinstance(summary, Mapping):
        for key, value in summary.items():
            if not str(key).endswith("_path"):
                continue
            if value and Path(str(value)).exists():
                artifacts.append(context.artifact_store.link_existing(Path(str(value)), mime=_artifact_mime(str(value))))
    return NodeExecutionResult(
        outputs={"result": payload},
        artifacts=artifacts,
        trace={
            "live_mcp": True,
            "server": node.tools[0].server,
            "tool": node.tools[0].tool,
            "selected_repo": payload.get("selected_repo", "") if isinstance(payload, Mapping) else "",
        },
        cost=ExecutionCost(tool_calls=1, container_starts=1 if node.sandbox is not None else 0),
        confidence=confidence,
    )


def _deterministic_case_planner_handler(
    inputs: Mapping[str, Any],
    *,
    preferred_repo: str,
    summary: str,
) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    candidate_repos = list(hints.get("candidate_repos", []))
    selected_repo = preferred_repo
    for repo in candidate_repos:
        repo_name = str(repo.get("name", ""))
        if repo_name == preferred_repo:
            selected_repo = repo_name
            break
    analysis_config = dict(hints.get("analysis_config", {}))
    return NodeExecutionResult(
        outputs={
            "selected_repo": selected_repo,
            "analysis_config": analysis_config,
            "summary": summary,
        },
        trace={"deterministic_case_planner": True, "candidate_repo_count": len(candidate_repos)},
        confidence=0.95,
    )


def _normalize_case_plan_outputs(
    outputs: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    preferred_repo: str,
    fallback_summary: str,
) -> dict[str, Any]:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    candidate_repos = list(hints.get("candidate_repos", []))
    allowed_repo_names = [str(repo.get("name", "")).strip() for repo in candidate_repos if str(repo.get("name", "")).strip()]
    default_repo = preferred_repo if preferred_repo in allowed_repo_names else (allowed_repo_names[0] if allowed_repo_names else preferred_repo)
    selected_repo = str(outputs.get("selected_repo", "")).strip() or default_repo
    if allowed_repo_names and selected_repo not in allowed_repo_names:
        selected_repo = default_repo

    analysis_config = dict(hints.get("analysis_config", {})) if isinstance(hints.get("analysis_config", {}), Mapping) else {}
    allowed_analysis_keys = set(analysis_config)
    proposed_config = outputs.get("analysis_config")
    if isinstance(proposed_config, Mapping):
        filtered = {
            str(key): value
            for key, value in dict(proposed_config).items()
            if not allowed_analysis_keys or str(key) in allowed_analysis_keys
        }
        analysis_config.update(filtered)

    normalized = {
        "selected_repo": selected_repo,
        "analysis_config": analysis_config,
        "summary": str(outputs.get("summary") or fallback_summary),
    }
    rationale = outputs.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        normalized["rationale"] = rationale.strip()
    return normalized


def _llm_first_case_handler(
    node: NodeSpec,
    inputs: Mapping[str, Any],
    context: Any,
    *,
    preferred_repo: str,
    fallback_summary: str,
) -> NodeExecutionResult:
    llm_result = context.llm_router.run_node(node, dict(inputs), context)
    if llm_result.failure_type == FailureType.none:
        normalized = _normalize_case_plan_outputs(
            llm_result.outputs if isinstance(llm_result.outputs, Mapping) else {},
            inputs,
            preferred_repo=preferred_repo,
            fallback_summary=fallback_summary,
        )
        trace = dict(llm_result.trace)
        trace["case_planner_strategy"] = "llm"
        return llm_result.model_copy(update={"outputs": normalized, "trace": trace})

    fallback = _deterministic_case_planner_handler(
        inputs,
        preferred_repo=preferred_repo,
        summary=fallback_summary,
    )
    fallback.trace.update(
        {
            "case_planner_strategy": "deterministic_fallback",
            "llm_failure_type": llm_result.failure_type.value,
            "llm_error": llm_result.error or "",
        }
    )
    return fallback


def _deterministic_visium_multi_agent_planner(inputs: Mapping[str, Any]) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    dataset_label = str(hints.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    scanpy_candidates = list(hints.get("scanpy_candidate_repos", []))
    squidpy_candidates = list(hints.get("squidpy_candidate_repos", []))
    scanpy_repo = "scanpy"
    squidpy_repo = "squidpy"
    for repo in scanpy_candidates:
        repo_name = str(repo.get("name", "")).strip()
        if repo_name == "scanpy":
            scanpy_repo = repo_name
            break
    for repo in squidpy_candidates:
        repo_name = str(repo.get("name", "")).strip()
        if repo_name == "squidpy":
            squidpy_repo = repo_name
            break
    return NodeExecutionResult(
        outputs={
            "scanpy_repo": scanpy_repo,
            "cluster_analysis_config": dict(hints.get("cluster_analysis_config", {})),
            "squidpy_repo": squidpy_repo,
            "interaction_analysis_config": dict(hints.get("interaction_analysis_config", {})),
            "summary": f"Fixed planner selected Scanpy clustering followed by Squidpy interaction analysis for the {dataset_label} task.",
        },
        trace={
            "deterministic_case_planner": True,
            "scanpy_candidate_count": len(scanpy_candidates),
            "squidpy_candidate_count": len(squidpy_candidates),
        },
        confidence=0.95,
    )


def _normalize_visium_multi_agent_plan_outputs(outputs: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    scanpy_candidates = {
        str(repo.get("name", "")).strip()
        for repo in list(hints.get("scanpy_candidate_repos", []))
        if str(repo.get("name", "")).strip()
    }
    squidpy_candidates = {
        str(repo.get("name", "")).strip()
        for repo in list(hints.get("squidpy_candidate_repos", []))
        if str(repo.get("name", "")).strip()
    }
    scanpy_repo = str(outputs.get("scanpy_repo", "")).strip() or "scanpy"
    squidpy_repo = str(outputs.get("squidpy_repo", "")).strip() or "squidpy"
    if scanpy_candidates and scanpy_repo not in scanpy_candidates:
        scanpy_repo = "scanpy" if "scanpy" in scanpy_candidates else sorted(scanpy_candidates)[0]
    if squidpy_candidates and squidpy_repo not in squidpy_candidates:
        squidpy_repo = "squidpy" if "squidpy" in squidpy_candidates else sorted(squidpy_candidates)[0]

    cluster_analysis_config = dict(hints.get("cluster_analysis_config", {}))
    proposed_cluster = outputs.get("cluster_analysis_config")
    if isinstance(proposed_cluster, Mapping):
        allowed = set(cluster_analysis_config)
        cluster_analysis_config.update(
            {str(key): value for key, value in dict(proposed_cluster).items() if not allowed or str(key) in allowed}
        )

    interaction_analysis_config = dict(hints.get("interaction_analysis_config", {}))
    proposed_interaction = outputs.get("interaction_analysis_config")
    if isinstance(proposed_interaction, Mapping):
        allowed = set(interaction_analysis_config)
        interaction_analysis_config.update(
            {str(key): value for key, value in dict(proposed_interaction).items() if not allowed or str(key) in allowed}
        )

    return {
        "scanpy_repo": scanpy_repo,
        "cluster_analysis_config": cluster_analysis_config,
        "squidpy_repo": squidpy_repo,
        "interaction_analysis_config": interaction_analysis_config,
        "summary": str(outputs.get("summary") or "Planner selected the multi-agent collaboration path."),
    }


def _visium_multi_agent_planner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    planner_mode = str(hints.get("planner_mode", "fixed")).strip()
    if planner_mode == "fixed":
        return _deterministic_visium_multi_agent_planner(inputs)

    llm_result = context.llm_router.run_node(node, dict(inputs), context)
    if llm_result.failure_type == FailureType.none:
        normalized = _normalize_visium_multi_agent_plan_outputs(
            llm_result.outputs if isinstance(llm_result.outputs, Mapping) else {},
            inputs,
        )
        trace = dict(llm_result.trace)
        trace["case_planner_strategy"] = "llm"
        return llm_result.model_copy(update={"outputs": normalized, "trace": trace})

    fallback = _deterministic_visium_multi_agent_planner(inputs)
    fallback.trace.update(
        {
            "case_planner_strategy": "deterministic_fallback",
            "llm_failure_type": llm_result.failure_type.value,
            "llm_error": llm_result.error or "",
        }
    )
    return fallback


def _case_refiner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    current_repo = str(inputs.get("selected_repo", "")).strip() or "scanpy"
    llm_result = context.llm_router.run_node(node, dict(inputs), context)
    fallback_summary = "Fallback refiner preserved the original plan while recording reviewer concerns."
    if llm_result.failure_type == FailureType.none:
        normalized = _normalize_case_plan_outputs(
            llm_result.outputs if isinstance(llm_result.outputs, Mapping) else {},
            inputs,
            preferred_repo=current_repo,
            fallback_summary=fallback_summary,
        )
        trace = dict(llm_result.trace)
        trace["case_planner_strategy"] = "llm_refiner"
        return llm_result.model_copy(update={"outputs": normalized, "trace": trace})

    fallback_outputs = _normalize_case_plan_outputs(
        {
            "selected_repo": current_repo,
            "analysis_config": inputs.get("analysis_config", {}),
            "summary": fallback_summary,
        },
        inputs,
        preferred_repo=current_repo,
        fallback_summary=fallback_summary,
    )
    return NodeExecutionResult(
        outputs=fallback_outputs,
        trace={
            "case_planner_strategy": "deterministic_refiner_fallback",
            "llm_failure_type": llm_result.failure_type.value,
            "llm_error": llm_result.error or "",
        },
        confidence=0.7,
    )


def _scanpy_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _sandbox_tool_handler(node, inputs, context)


def _paper_specialist_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    result = _sandbox_tool_handler(node, inputs, context)
    if result.failure_type != FailureType.none:
        return result
    payload = dict(result.outputs.get("result", {})) if isinstance(result.outputs.get("result", {}), Mapping) else {}
    if str(payload.get("status", "")).strip().lower() == "error":
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"Specialist tool {node.node_id} failed: {payload.get('message', 'unknown specialist error')}",
            trace={"live_mcp": True, "specialist_node": node.node_id, "payload_preview": payload},
            cost=result.cost,
        )
    specialist_payload = payload.get("result", {}) if isinstance(payload.get("result", {}), Mapping) else {}
    if not isinstance(specialist_payload, Mapping) or not specialist_payload:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"Specialist tool {node.node_id} returned an empty or unexpected payload.",
            trace={"live_mcp": True, "specialist_node": node.node_id, "payload_preview": payload},
            cost=result.cost,
        )
    result.outputs = dict(specialist_payload)
    role = str(payload.get("role", "")).strip()
    if role:
        result.trace["specialist_role"] = role
    return result


def _run_direct_case_study_job(
    node: NodeSpec,
    *,
    context: Any,
    cmd: Sequence[str],
    summary_path: str,
    selected_repo: str,
    tool_module: str,
    extra_payload: Optional[Mapping[str, Any]] = None,
) -> NodeExecutionResult:
    if node.sandbox is None:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error="Direct sandbox execution requires node.sandbox",
            trace={"live_direct_sandbox": True, "tool_module": tool_module},
        )

    completed = context.sandbox_runner.run_in_sandbox(
        node.sandbox,
        list(cmd),
        timeout_s=int(node.sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"Direct sandbox tool failed: {completed.stderr or completed.stdout}",
            trace={"live_direct_sandbox": True, "tool_module": tool_module, "selected_repo": selected_repo},
        )

    summary = None
    if summary_path and Path(summary_path).exists():
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = None
    if summary is None:
        try:
            summary = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return NodeExecutionResult(
                failure_type=FailureType.tool_runtime_error,
                error="Direct sandbox tool produced non-JSON output and no usable summary artifact was found.",
                trace={
                    "live_direct_sandbox": True,
                    "tool_module": tool_module,
                    "stdout_preview": completed.stdout[:500],
                    "stderr_preview": completed.stderr[:500],
                    "summary_path": summary_path,
                },
            )

    payload: dict[str, Any] = {
        "status": "ok",
        "selected_repo": selected_repo,
        "summary": summary,
        "workflow_meta": {"tool_execution_mode": "direct_sandbox"},
    }
    if extra_payload:
        payload.update(dict(extra_payload))
    artifacts = []
    if isinstance(summary, Mapping):
        for key, value in summary.items():
            if not str(key).endswith("_path"):
                continue
            if value and Path(str(value)).exists():
                artifacts.append(context.artifact_store.link_existing(Path(str(value)), mime=_artifact_mime(str(value))))
    return NodeExecutionResult(
        outputs={"result": payload},
        artifacts=artifacts,
        trace={"live_direct_sandbox": True, "selected_repo": selected_repo, "tool_module": tool_module},
        cost=ExecutionCost(tool_calls=1, container_starts=1 if node.sandbox is not None else 0),
        confidence=0.82,
    )


def _scanpy_visium_cluster_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    selected_repo = str(inputs.get("scanpy_repo", "")).strip() or "scanpy"
    analysis_config = (
        dict(inputs.get("cluster_analysis_config", {}))
        if isinstance(inputs.get("cluster_analysis_config", {}), Mapping)
        else dict(hints.get("cluster_analysis_config", {}))
    )
    return _run_direct_case_study_job(
        node,
        context=context,
        cmd=[
            "python",
            "-m",
            "agentcoop.case_studies.scanpy_visium_cluster_job",
            "--output-dir",
            str(hints["output_dir"]),
            "--cluster-figure-path",
            str(hints["cluster_figure_path"]),
            "--marker-figure-path",
            str(hints["marker_figure_path"]),
            "--summary-path",
            str(hints["cluster_summary_path"]),
            "--annotated-data-path",
            str(hints["annotated_data_path"]),
            "--raw-data-path",
            str(hints["raw_data_path"]),
            "--selected-repo",
            selected_repo,
            "--config-json",
            json.dumps(analysis_config, ensure_ascii=True),
            "--cluster-title",
            str(hints.get("cluster_title", f"{hints.get('dataset_label', 'Spatial dataset')} Scanpy Clusters")),
            "--marker-title",
            str(hints.get("marker_title", f"{hints.get('dataset_label', 'Spatial dataset')} Marker Heatmap")),
        ],
        summary_path=str(hints["cluster_summary_path"]),
        selected_repo=selected_repo,
        tool_module="agentcoop.case_studies.scanpy_visium_cluster_job",
    )


def _visium_squidpy_interaction_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    selected_repo = str(inputs.get("squidpy_repo", "")).strip() or "squidpy"
    analysis_config = (
        dict(inputs.get("interaction_analysis_config", {}))
        if isinstance(inputs.get("interaction_analysis_config", {}), Mapping)
        else dict(hints.get("interaction_analysis_config", {}))
    )
    input_h5ad_path = str(inputs.get("input_h5ad_path", "")).strip() or str(hints["annotated_data_path"])
    cluster_key = str(inputs.get("cluster_key", "")).strip()
    if cluster_key:
        analysis_config["cluster_key"] = cluster_key
    return _run_direct_case_study_job(
        node,
        context=context,
        cmd=[
            "python",
            "-m",
            "agentcoop.case_studies.squidpy_visium_interactions_job",
            "--output-dir",
            str(hints["output_dir"]),
            "--figure-path",
            str(hints["spatial_figure_path"]),
            "--interaction-figure-path",
            str(hints["interaction_figure_path"]),
            "--summary-path",
            str(hints["interaction_summary_path"]),
            "--raw-data-path",
            str(hints["raw_data_path"]),
            "--input-h5ad-path",
            input_h5ad_path,
            "--selected-repo",
            selected_repo,
            "--config-json",
            json.dumps(analysis_config, ensure_ascii=True),
            "--title",
            str(hints.get("interaction_title", f"{hints.get('dataset_label', 'Spatial dataset')} Neighborhood Enrichment")),
        ],
        summary_path=str(hints["interaction_summary_path"]),
        selected_repo=selected_repo,
        tool_module="agentcoop.case_studies.squidpy_visium_interactions_job",
    )


def _visium_multi_agent_monolith_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    scanpy_repo = str(inputs.get("scanpy_repo", "")).strip() or "scanpy"
    squidpy_repo = str(inputs.get("squidpy_repo", "")).strip() or "squidpy"
    cluster_analysis_config = (
        dict(inputs.get("cluster_analysis_config", {}))
        if isinstance(inputs.get("cluster_analysis_config", {}), Mapping)
        else dict(hints.get("cluster_analysis_config", {}))
    )
    interaction_analysis_config = (
        dict(inputs.get("interaction_analysis_config", {}))
        if isinstance(inputs.get("interaction_analysis_config", {}), Mapping)
        else dict(hints.get("interaction_analysis_config", {}))
    )
    return _run_direct_case_study_job(
        node,
        context=context,
        cmd=[
            "python",
            "-m",
            "agentcoop.case_studies.visium_multi_agent_monolith_job",
            "--output-dir",
            str(hints["output_dir"]),
            "--cluster-figure-path",
            str(hints["cluster_figure_path"]),
            "--marker-figure-path",
            str(hints["marker_figure_path"]),
            "--cluster-summary-path",
            str(hints["cluster_summary_path"]),
            "--annotated-data-path",
            str(hints["annotated_data_path"]),
            "--spatial-figure-path",
            str(hints["spatial_figure_path"]),
            "--interaction-figure-path",
            str(hints["interaction_figure_path"]),
            "--interaction-summary-path",
            str(hints["interaction_summary_path"]),
            "--summary-path",
            str(hints["summary_path"]),
            "--raw-data-path",
            str(hints["raw_data_path"]),
            "--scanpy-repo",
            scanpy_repo,
            "--squidpy-repo",
            squidpy_repo,
            "--cluster-config-json",
            json.dumps(cluster_analysis_config, ensure_ascii=True),
            "--interaction-config-json",
            json.dumps(interaction_analysis_config, ensure_ascii=True),
            "--cluster-title",
            str(hints.get("cluster_title", f"{hints.get('dataset_label', 'Spatial dataset')} Scanpy Clusters")),
            "--marker-title",
            str(hints.get("marker_title", f"{hints.get('dataset_label', 'Spatial dataset')} Marker Heatmap")),
            "--spatial-title",
            str(hints.get("spatial_title", f"{hints.get('dataset_label', 'Spatial dataset')} Spatial Layout")),
            "--interaction-title",
            str(hints.get("interaction_title", f"{hints.get('dataset_label', 'Spatial dataset')} Neighborhood Enrichment")),
        ],
        summary_path=str(hints["summary_path"]),
        selected_repo=f"{scanpy_repo}+{squidpy_repo}",
        tool_module="agentcoop.case_studies.visium_multi_agent_monolith_job",
        extra_payload={"scanpy_repo": scanpy_repo, "squidpy_repo": squidpy_repo},
    )


def _squidpy_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    case_id = str(hints.get("case_id", "squidpy_visium_hne_spatial")).strip()
    if case_id not in {"squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}:
        return _sandbox_tool_handler(node, inputs, context)

    if node.sandbox is None:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error="Direct sandbox execution requires node.sandbox",
            trace={"live_direct_sandbox": True},
        )

    selected_repo = str(inputs.get("selected_repo", "")).strip() or "squidpy"
    analysis_config = (
        dict(inputs.get("analysis_config", {}))
        if isinstance(inputs.get("analysis_config", {}), Mapping)
        else dict(hints.get("analysis_config", {}))
    )
    cmd = [
        "python",
        "-m",
        "agentcoop.case_studies.squidpy_visium_interactions_job",
        "--output-dir",
        str(hints["output_dir"]),
        "--figure-path",
        str(hints["figure_path"]),
        "--interaction-figure-path",
        str(hints.get("interaction_figure_path", "")),
        "--summary-path",
        str(hints["summary_path"]),
        "--raw-data-path",
        str(hints.get("raw_data_path", "")),
        "--selected-repo",
        selected_repo,
        "--config-json",
        json.dumps(analysis_config, ensure_ascii=True),
        "--title",
        str(hints.get("figure_title", "Visium Neighborhood Enrichment")),
    ]
    completed = context.sandbox_runner.run_in_sandbox(
        node.sandbox,
        cmd,
        timeout_s=int(node.sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"Direct sandbox tool failed: {completed.stderr or completed.stdout}",
            trace={
                "live_direct_sandbox": True,
                "selected_repo": selected_repo,
            },
        )

    summary = None
    summary_path = str(hints.get("summary_path", "")).strip()
    if summary_path and Path(summary_path).exists():
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = None
    if summary is None:
        try:
            summary = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return NodeExecutionResult(
                failure_type=FailureType.tool_runtime_error,
                error="Direct sandbox tool produced non-JSON output and no usable summary artifact was found.",
                trace={
                    "live_direct_sandbox": True,
                    "stdout_preview": completed.stdout[:500],
                    "stderr_preview": completed.stderr[:500],
                    "summary_path": summary_path,
                },
            )

    payload = {
        "status": "ok",
        "selected_repo": selected_repo,
        "summary": summary,
        "workflow_meta": {"tool_execution_mode": "direct_sandbox"},
    }
    cluster_count = int(summary.get("cluster_count", 0) or 0) if isinstance(summary, Mapping) else 0
    confidence = min(0.98, 0.55 + 0.05 * min(cluster_count, 8))
    artifacts = []
    if isinstance(summary, Mapping):
        for key, value in summary.items():
            if not str(key).endswith("_path"):
                continue
            if value and Path(str(value)).exists():
                artifacts.append(context.artifact_store.link_existing(Path(str(value)), mime=_artifact_mime(str(value))))
    return NodeExecutionResult(
        outputs={"result": payload},
        artifacts=artifacts,
        trace={
            "live_direct_sandbox": True,
            "selected_repo": selected_repo,
            "tool_module": "agentcoop.case_studies.squidpy_visium_interactions_job",
        },
        cost=ExecutionCost(tool_calls=1, container_starts=1 if node.sandbox is not None else 0),
        confidence=confidence,
    )


def _squidpy_tool_handler_legacy(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _sandbox_tool_handler(node, inputs, context)


def _scanpy_planner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _llm_first_case_handler(
        node,
        inputs,
        context,
        preferred_repo="scanpy",
        fallback_summary="Fallback planner selected the executable Scanpy repo and preserved the configured PBMC3k pipeline.",
    )


def _scanpy_paul15_planner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    planner_mode = str(hints.get("planner_mode", "llm_first")).strip().lower()
    dataset_label = str(hints.get("dataset_label", "Paul15")).strip() or "Paul15"
    if planner_mode == "fixed":
        return _deterministic_case_planner_handler(
            inputs,
            preferred_repo="scanpy",
            summary=f"Fixed planner selected the executable Scanpy repo and preserved the configured {dataset_label} trajectory pipeline.",
        )
    return _llm_first_case_handler(
        node,
        inputs,
        context,
        preferred_repo="scanpy",
        fallback_summary=(f"Fallback planner selected the executable Scanpy repo and preserved the configured {dataset_label} trajectory pipeline."),
    )


def _scanpy_paul15_review_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    del node, context
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    dataset_id = str(hints.get("dataset_id", "paul15")).strip().lower()
    requires_gene_panel = bool(str(hints.get("gene_trend_figure_path", "")).strip())
    tool_result = dict(inputs.get("tool_result", {})) if isinstance(inputs.get("tool_result", {}), Mapping) else {}
    tool_summary = dict(tool_result.get("summary", {})) if isinstance(tool_result.get("summary", {}), Mapping) else {}
    if not tool_summary:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error="Review handler expected tool_result.summary but none was provided.",
            trace={"deterministic_review": True},
        )

    reference_summary: dict[str, Any] = {}
    reference_path = Path(str(hints.get("reference_summary_path", "")).strip())
    if reference_path.exists():
        try:
            reference_summary = json.loads(reference_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            reference_summary = {}

    methodology = 9
    biological = 8
    artifact = 9
    concerns: list[str] = []

    cluster_count = int(tool_summary.get("cluster_count", 0) or 0)
    paga_edge_count = int(tool_summary.get("paga_edge_count", 0) or 0)
    ref_cluster_count = int(reference_summary.get("cluster_count", cluster_count) or cluster_count)
    ref_paga_edge_count = int(reference_summary.get("paga_edge_count", paga_edge_count) or paga_edge_count)
    if abs(cluster_count - ref_cluster_count) > 4:
        methodology -= 2
        concerns.append("Cluster count diverges from reference summary.")
    if abs(paga_edge_count - ref_paga_edge_count) > 10:
        methodology -= 1
        concerns.append("PAGA edge count diverges from reference summary.")

    pseudotime_range = tool_summary.get("pseudotime_range", [])
    if (
        not isinstance(pseudotime_range, list)
        or len(pseudotime_range) != 2
        or not all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in pseudotime_range)
        or float(pseudotime_range[1]) <= float(pseudotime_range[0])
    ):
        biological -= 3
        concerns.append("Pseudotime range is malformed or non-finite.")

    root_provenance = tool_summary.get("root_provenance", {})
    if isinstance(root_provenance, Mapping) and bool(root_provenance.get("nonfinite_dpt_replaced", False)):
        concerns.append("Pseudotime required a finite fallback; record this adaptation in the methods.")
        if dataset_id != "moignard15":
            biological -= 1

    dynamic_summary = tool_summary.get("dynamic_gene_summary", {})
    max_abs_corr = 0.0
    if isinstance(dynamic_summary, Mapping):
        try:
            max_abs_corr = float(dynamic_summary.get("max_abs_correlation", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_abs_corr = 0.0
    if requires_gene_panel:
        panel_genes = tool_summary.get("dynamic_gene_panel_genes", [])
        if not isinstance(panel_genes, list) or len(panel_genes) < 2:
            artifact -= 3
            concerns.append("Dynamic-gene panel genes are missing or too short.")
        if not str(tool_summary.get("dynamic_gene_panel_table_path", "")).strip():
            artifact -= 2
            concerns.append("Dynamic-gene panel table path is missing.")
        if max_abs_corr < 0.05:
            biological -= 1
            concerns.append("Dynamic-gene correlations are weak.")

    if dataset_id == "moignard15":
        if str(tool_summary.get("dataset_group_key", "")).strip() != "exp_groups":
            methodology -= 2
            concerns.append("Moignard15 group metadata was not preserved.")
        if int(tool_summary.get("dataset_group_count", 0) or 0) != 5:
            methodology -= 1
            concerns.append("Moignard15 stage count does not match expected metadata.")
    elif dataset_id == "krumsiek11":
        if str(tool_summary.get("dataset_group_key", "")).strip() != "cell_type":
            methodology -= 2
            concerns.append("Krumsiek11 cell-type metadata was not preserved.")
        if int(tool_summary.get("dataset_group_count", 0) or 0) != 5:
            methodology -= 1
            concerns.append("Krumsiek11 cell-type count does not match expected metadata.")

    methodology = max(1, methodology)
    biological = max(1, biological)
    artifact = max(1, artifact)
    overall = "accept" if min(methodology, biological, artifact) >= 8 else "partial_pass" if min(methodology, biological, artifact) >= 6 else "fail"
    return NodeExecutionResult(
        outputs={
            "methodology_faithfulness": methodology,
            "biological_plausibility": biological,
            "artifact_completeness": artifact,
            "overall_verdict": overall,
            "concerns": concerns,
            "summary": "Deterministic case-study reviewer completed an artifact- and summary-grounded assessment.",
        },
        trace={"deterministic_review": True, "dataset_id": dataset_id},
        confidence=0.78,
        cost=ExecutionCost(),
    )


def _squidpy_planner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = dict(inputs.get("task", {}))
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    planner_mode = str(hints.get("planner_mode", "llm_first")).strip().lower()
    case_id = str(hints.get("case_id", "squidpy_visium_hne_spatial")).strip()
    dataset_label = str(hints.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    interaction_case = case_id in {"squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}
    if planner_mode == "fixed":
        summary = (
            f"Fixed planner selected the executable Squidpy repo and preserved the configured {dataset_label} interaction-analysis pipeline."
            if interaction_case
            else f"Fixed planner selected the executable Squidpy repo and preserved the configured {dataset_label} plotting pipeline."
        )
        return _deterministic_case_planner_handler(
            inputs,
            preferred_repo="squidpy",
            summary=summary,
        )
    return _llm_first_case_handler(
        node,
        inputs,
        context,
        preferred_repo="squidpy",
        fallback_summary=(
            f"Fallback planner selected the executable Squidpy repo and preserved the configured {dataset_label} interaction-analysis pipeline."
            if interaction_case
            else f"Fallback planner selected the executable Squidpy repo and preserved the configured {dataset_label} plotting pipeline."
        ),
    )


def _preferred_case_node_outputs(report: ExecutionReport, *node_ids: str) -> dict[str, Any]:
    for node_id in node_ids:
        outputs = report.node_results.get(node_id, NodeExecutionResult()).outputs
        if isinstance(outputs, Mapping) and outputs:
            return dict(outputs)
    return {}


def _summarize_scanpy_case_study(
    report: ExecutionReport,
    *,
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
) -> dict[str, Any]:
    planner_output = _preferred_case_node_outputs(report, "planner")
    refiner_output = _preferred_case_node_outputs(report, "case_refiner")
    tool_payload = _preferred_case_node_outputs(report, "scanpy_tool_refined", "scanpy_tool").get("result", {})
    reviewer_output = _preferred_case_node_outputs(report, "bio_reviewer_refined", "bio_reviewer")
    tool_summary = tool_payload.get("summary", {}) if isinstance(tool_payload, Mapping) else {}
    return {
        "benchmark": "case_study",
        "case_id": case_assets["case_id"],
        "success": report.success,
        "failure_type": report.failure_type.value,
        "summary": report.summary,
        "workflow_signature": _workflow_signature(report),
        "confidence": report.confidence,
        "cost": report.cost.model_dump(),
        "activated_gates": list(report.activated_gates),
        "active_subgraphs": list(report.active_subgraphs),
        "hard_checks": [item.model_dump(mode="json") for item in report.hard_checks],
        "contract_violations": [item.model_dump(mode="json") for item in report.contract_violations],
        "blame_candidates": [item.model_dump(mode="json") for item in report.blame_candidates],
        "planner_output": planner_output,
        "refiner_output": refiner_output,
        "tool_output": tool_payload,
        "review_output": reviewer_output,
        "used_refinement": bool(refiner_output),
        "generated_figure_path": tool_summary.get("figure_path", ""),
        "generated_summary_path": tool_summary.get("summary_path", ""),
        "generated_raw_data_path": tool_summary.get("raw_data_path", ""),
        "reference_figure_path": reference_assets["reference_figure_path"],
        "reference_summary_path": reference_assets["reference_summary_path"],
    }


def render_scanpy_case_study_analysis(summary: Mapping[str, Any]) -> str:
    review_output = summary.get("review_output", {})
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    lines = [
        "# Scanpy PBMC3k case-study analysis",
        "",
        f"- Success: {summary.get('success', False)}",
        f"- Failure type: {summary.get('failure_type', 'none')}",
        f"- Workflow signature: {summary.get('workflow_signature', '')}",
        f"- Confidence: {summary.get('confidence', 0.0)}",
        f"- Cost: {json.dumps(summary.get('cost', {}), ensure_ascii=True)}",
        f"- Generated figure: {summary.get('generated_figure_path', '')}",
        f"- Generated summary: {summary.get('generated_summary_path', '')}",
        f"- Reference figure: {summary.get('reference_figure_path', '')}",
        f"- Hard checks: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
        f"- Used refinement: {summary.get('used_refinement', False)}",
        "",
        "## Planner",
        f"- Output: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
    ]
    if summary.get("refiner_output"):
        lines.extend(["", "## Refiner", f"- Output: {json.dumps(summary.get('refiner_output', {}), ensure_ascii=True)}"])
    lines.extend(
        [
            "",
            "## Tool Result",
            f"- Selected repo: {tool_output.get('selected_repo', '') if isinstance(tool_output, Mapping) else ''}",
            f"- Tool status: {tool_output.get('status', '') if isinstance(tool_output, Mapping) else ''}",
            f"- Cluster count: {tool_summary.get('cluster_count', '') if isinstance(tool_summary, Mapping) else ''}",
            (
                f"- Figure size: {tool_summary.get('figure_width', '')}x{tool_summary.get('figure_height', '')}"
                if isinstance(tool_summary, Mapping)
                else "- Figure size: "
            ),
            "",
            "## Reviewer",
            f"- Output: {json.dumps(review_output, ensure_ascii=True)}",
        ]
    )
    if summary.get("contract_violations"):
        lines.extend(["", "## Contract Violations"])
        for item in summary["contract_violations"]:
            lines.append(f"- {item['node_id']}: {item['message']}")
    return "\n".join(lines) + "\n"


def render_scanpy_case_study_narrative(summary: Mapping[str, Any]) -> str:
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    review_output = summary.get("review_output", {})
    selected_repo = tool_output.get("selected_repo", "") if isinstance(tool_output, Mapping) else ""
    cluster_count = tool_summary.get("cluster_count", "") if isinstance(tool_summary, Mapping) else ""
    return "\n".join(
        [
            "# Scanpy PBMC3k Experiment 3 Narrative",
            "",
            "This run used a sandboxed Scanpy environment cloned from an external candidate repository, "
            "wrapped as an MCP tool, to reproduce a PBMC3k UMAP figure from a methods excerpt and target figure description.",
            "",
            f"Planner choice: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
            (
                f"Refiner output: {json.dumps(summary.get('refiner_output', {}), ensure_ascii=True)}"
                if summary.get("refiner_output")
                else "Refiner output: not used"
            ),
            f"Sandboxed tool result: repo={selected_repo}, clusters={cluster_count}, figure={summary.get('generated_figure_path', '')}",
            f"Reviewer assessment: {json.dumps(review_output, ensure_ascii=True)}",
            f"Hard-check status: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
            "",
            f"Final status: success={summary.get('success', False)} failure_type={summary.get('failure_type', 'none')}",
        ]
    ) + "\n"


def _summarize_scanpy_paul15_case_study(
    report: ExecutionReport,
    *,
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
) -> dict[str, Any]:
    paper_figure_case = bool(str(case_assets.get("reference_gene_trend_figure_path", "")).strip())
    dataset_label = str(case_assets.get("dataset_label", "Paul15"))
    transfer_case = str(case_assets.get("dataset_id", "paul15")) != "paul15"
    planner_output = _preferred_case_node_outputs(report, "planner")
    paper_spec_output = _preferred_case_node_outputs(report, "paper_spec")
    refiner_output = _preferred_case_node_outputs(report, "case_refiner")
    tool_payload = _preferred_case_node_outputs(
        report,
        "scanpy_trajectory_tool_refined",
        "scanpy_trajectory_tool",
    ).get("result", {})
    reviewer_output = _preferred_case_node_outputs(report, "bio_reviewer_refined", "bio_reviewer")
    tool_summary = tool_payload.get("summary", {}) if isinstance(tool_payload, Mapping) else {}
    return {
        "benchmark": "case_study",
        "case_id": case_assets["case_id"],
        "success": report.success,
        "failure_type": report.failure_type.value,
        "summary": report.summary,
        "workflow_signature": _workflow_signature(report),
        "confidence": report.confidence,
        "cost": report.cost.model_dump(),
        "activated_gates": list(report.activated_gates),
        "active_subgraphs": list(report.active_subgraphs),
        "hard_checks": [item.model_dump(mode="json") for item in report.hard_checks],
        "contract_violations": [item.model_dump(mode="json") for item in report.contract_violations],
        "blame_candidates": [item.model_dump(mode="json") for item in report.blame_candidates],
        "paper_spec_output": paper_spec_output,
        "planner_output": planner_output,
        "refiner_output": refiner_output,
        "tool_output": tool_payload,
        "review_output": reviewer_output,
        "used_refinement": bool(refiner_output),
        "generated_figure_path": tool_summary.get("figure_path", ""),
        "generated_paga_figure_path": tool_summary.get("paga_figure_path", ""),
        "generated_gene_trend_figure_path": tool_summary.get("gene_trend_figure_path", ""),
        "generated_summary_path": tool_summary.get("summary_path", ""),
        "generated_raw_data_path": tool_summary.get("raw_data_path", ""),
        "reference_figure_path": reference_assets["reference_figure_path"],
        "reference_paga_figure_path": reference_assets["reference_paga_figure_path"],
        "reference_gene_trend_figure_path": reference_assets.get("reference_gene_trend_figure_path", ""),
        "reference_summary_path": reference_assets["reference_summary_path"],
        "paper_figure_case": paper_figure_case,
        "dataset_label": dataset_label,
        "transfer_case": transfer_case,
    }


def render_scanpy_paul15_case_study_analysis(summary: Mapping[str, Any]) -> str:
    paper_figure_case = bool(summary.get("paper_figure_case", False))
    dataset_label = str(summary.get("dataset_label", "Paul15"))
    review_output = summary.get("review_output", {})
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    lines = [
        f"# Scanpy {dataset_label} trajectory case-study analysis",
        "",
        f"- Success: {summary.get('success', False)}",
        f"- Failure type: {summary.get('failure_type', 'none')}",
        f"- Workflow signature: {summary.get('workflow_signature', '')}",
        f"- Confidence: {summary.get('confidence', 0.0)}",
        f"- Cost: {json.dumps(summary.get('cost', {}), ensure_ascii=True)}",
        f"- Generated trajectory figure: {summary.get('generated_figure_path', '')}",
        f"- Generated PAGA figure: {summary.get('generated_paga_figure_path', '')}",
        f"- Generated dynamic-gene figure: {summary.get('generated_gene_trend_figure_path', '')}",
        f"- Generated summary: {summary.get('generated_summary_path', '')}",
        f"- Reference trajectory figure: {summary.get('reference_figure_path', '')}",
        f"- Reference PAGA figure: {summary.get('reference_paga_figure_path', '')}",
        f"- Reference dynamic-gene figure: {summary.get('reference_gene_trend_figure_path', '')}",
        f"- Hard checks: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
        f"- Used refinement: {summary.get('used_refinement', False)}",
        "",
        "## Paper Spec",
        f"- Output: {json.dumps(summary.get('paper_spec_output', {}), ensure_ascii=True)}",
        "",
        "## Planner",
        f"- Output: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
    ]
    if summary.get("refiner_output"):
        lines.extend(["", "## Refiner", f"- Output: {json.dumps(summary.get('refiner_output', {}), ensure_ascii=True)}"])
    lines.extend(
        [
            "",
            "## Tool Result",
            f"- Selected repo: {tool_output.get('selected_repo', '') if isinstance(tool_output, Mapping) else ''}",
            f"- Tool status: {tool_output.get('status', '') if isinstance(tool_output, Mapping) else ''}",
            f"- Cluster count: {tool_summary.get('cluster_count', '') if isinstance(tool_summary, Mapping) else ''}",
            f"- Pseudotime range: {json.dumps(tool_summary.get('pseudotime_range', []), ensure_ascii=True)}",
            f"- PAGA edge count: {tool_summary.get('paga_edge_count', '') if isinstance(tool_summary, Mapping) else ''}",
            f"- Root cluster: {tool_summary.get('root_cluster', '') if isinstance(tool_summary, Mapping) else ''}",
            f"- QC summary: {json.dumps(tool_summary.get('qc_summary', {}), ensure_ascii=True)}",
            f"- Dynamic genes: {json.dumps(tool_summary.get('dynamic_gene_summary', {}), ensure_ascii=True)}",
            (
                f"- Dynamic-gene panel genes: {json.dumps(tool_summary.get('dynamic_gene_panel_genes', []), ensure_ascii=True)}"
                if paper_figure_case
                else "- Dynamic-gene panel genes: []"
            ),
            "",
            "## Reviewer",
            f"- Output: {json.dumps(review_output, ensure_ascii=True)}",
        ]
    )
    if summary.get("contract_violations"):
        lines.extend(["", "## Contract Violations"])
        for item in summary["contract_violations"]:
            lines.append(f"- {item['node_id']}: {item['message']}")
    return "\n".join(lines) + "\n"


def render_scanpy_paul15_case_study_narrative(summary: Mapping[str, Any]) -> str:
    paper_figure_case = bool(summary.get("paper_figure_case", False))
    dataset_label = str(summary.get("dataset_label", "Paul15"))
    transfer_case = bool(summary.get("transfer_case", False))
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    review_output = summary.get("review_output", {})
    selected_repo = tool_output.get("selected_repo", "") if isinstance(tool_output, Mapping) else ""
    cluster_count = tool_summary.get("cluster_count", "") if isinstance(tool_summary, Mapping) else ""
    pseudotime_range = tool_summary.get("pseudotime_range", []) if isinstance(tool_summary, Mapping) else []
    return "\n".join(
        [
            f"# Scanpy {dataset_label} Experiment 3 Narrative",
            "",
            (
                "This run used a sandboxed Scanpy environment cloned from an external candidate repository, "
                "wrapped as an MCP tool, to transfer a paper-grounded trajectory method to a second public dataset."
                if transfer_case
                else "This run used a sandboxed Scanpy environment cloned from an external candidate repository, "
                f"wrapped as an MCP tool, to reproduce a paper-style {dataset_label} figure package from a methods excerpt and target description."
                if paper_figure_case
                else "This run used a sandboxed Scanpy environment cloned from an external candidate repository, "
                f"wrapped as an MCP tool, to reconstruct a public {dataset_label} trajectory workflow from a methods excerpt and target description."
            ),
            "",
            f"Paper-spec output: {json.dumps(summary.get('paper_spec_output', {}), ensure_ascii=True)}",
            f"Planner choice: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
            (
                f"Refiner output: {json.dumps(summary.get('refiner_output', {}), ensure_ascii=True)}"
                if summary.get("refiner_output")
                else "Refiner output: not used"
            ),
            (
                "Sandboxed tool result: "
                f"repo={selected_repo}, clusters={cluster_count}, pseudotime_range={json.dumps(pseudotime_range, ensure_ascii=True)}, "
                f"trajectory_figure={summary.get('generated_figure_path', '')}, paga_figure={summary.get('generated_paga_figure_path', '')}, "
                f"dynamic_gene_figure={summary.get('generated_gene_trend_figure_path', '')}"
            ),
            f"QC summary: {json.dumps(tool_summary.get('qc_summary', {}), ensure_ascii=True)}",
            f"Dynamic/marker evidence: {json.dumps({'markers': tool_summary.get('marker_gene_summary', {}), 'dynamic': tool_summary.get('dynamic_gene_summary', {}), 'panel_genes': tool_summary.get('dynamic_gene_panel_genes', [])}, ensure_ascii=True)}",
            f"Reviewer assessment: {json.dumps(review_output, ensure_ascii=True)}",
            f"Hard-check status: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
            "",
            f"Final status: success={summary.get('success', False)} failure_type={summary.get('failure_type', 'none')}",
        ]
    ) + "\n"


def _summarize_squidpy_case_study(
    report: ExecutionReport,
    *,
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
) -> dict[str, Any]:
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    interaction_case = str(case_assets.get("case_id", "")) in {"squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}
    planner_output = _preferred_case_node_outputs(report, "planner")
    refiner_output = _preferred_case_node_outputs(report, "case_refiner")
    tool_payload = _preferred_case_node_outputs(report, "squidpy_tool_refined", "squidpy_tool").get("result", {})
    reviewer_output = _preferred_case_node_outputs(report, "bio_reviewer_refined", "bio_reviewer")
    tool_summary = tool_payload.get("summary", {}) if isinstance(tool_payload, Mapping) else {}
    return {
        "benchmark": "case_study",
        "case_id": case_assets["case_id"],
        "dataset_id": case_assets.get("dataset_id", ""),
        "dataset_label": dataset_label,
        "success": report.success,
        "failure_type": report.failure_type.value,
        "summary": report.summary,
        "workflow_signature": _workflow_signature(report),
        "confidence": report.confidence,
        "cost": report.cost.model_dump(),
        "activated_gates": list(report.activated_gates),
        "active_subgraphs": list(report.active_subgraphs),
        "hard_checks": [item.model_dump(mode="json") for item in report.hard_checks],
        "contract_violations": [item.model_dump(mode="json") for item in report.contract_violations],
        "blame_candidates": [item.model_dump(mode="json") for item in report.blame_candidates],
        "planner_output": planner_output,
        "refiner_output": refiner_output,
        "tool_output": tool_payload,
        "review_output": reviewer_output,
        "used_refinement": bool(refiner_output),
        "generated_figure_path": tool_summary.get("figure_path", ""),
        "generated_interaction_figure_path": tool_summary.get("interaction_figure_path", ""),
        "generated_summary_path": tool_summary.get("summary_path", ""),
        "generated_raw_data_path": tool_summary.get("raw_data_path", ""),
        "reference_figure_path": reference_assets["reference_figure_path"],
        "reference_interaction_figure_path": reference_assets.get("reference_interaction_figure_path", ""),
        "reference_summary_path": reference_assets["reference_summary_path"],
        "interaction_case": interaction_case,
    }


def render_squidpy_case_study_analysis(summary: Mapping[str, Any]) -> str:
    dataset_label = str(summary.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    review_output = summary.get("review_output", {})
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    lines = [
        f"# Squidpy {dataset_label} case-study analysis",
        "",
        f"- Success: {summary.get('success', False)}",
        f"- Failure type: {summary.get('failure_type', 'none')}",
        f"- Workflow signature: {summary.get('workflow_signature', '')}",
        f"- Confidence: {summary.get('confidence', 0.0)}",
        f"- Cost: {json.dumps(summary.get('cost', {}), ensure_ascii=True)}",
        f"- Generated figure: {summary.get('generated_figure_path', '')}",
        f"- Generated interaction figure: {summary.get('generated_interaction_figure_path', '')}",
        f"- Generated summary: {summary.get('generated_summary_path', '')}",
        f"- Reference figure: {summary.get('reference_figure_path', '')}",
        f"- Reference interaction figure: {summary.get('reference_interaction_figure_path', '')}",
        f"- Hard checks: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
        f"- Used refinement: {summary.get('used_refinement', False)}",
        "",
        "## Planner",
        f"- Output: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
    ]
    if summary.get("refiner_output"):
        lines.extend(["", "## Refiner", f"- Output: {json.dumps(summary.get('refiner_output', {}), ensure_ascii=True)}"])
    lines.extend(
        [
            "",
            "## Tool Result",
            f"- Selected repo: {tool_output.get('selected_repo', '') if isinstance(tool_output, Mapping) else ''}",
            f"- Tool status: {tool_output.get('status', '') if isinstance(tool_output, Mapping) else ''}",
            f"- Cluster count: {tool_summary.get('cluster_count', '') if isinstance(tool_summary, Mapping) else ''}",
            (
                f"- Figure size: {tool_summary.get('figure_width', '')}x{tool_summary.get('figure_height', '')}"
                if isinstance(tool_summary, Mapping)
                else "- Figure size: "
            ),
            f"- Spatial image metadata present: {tool_summary.get('has_spatial_image', '') if isinstance(tool_summary, Mapping) else ''}",
            f"- Has neighborhood enrichment: {tool_summary.get('has_nhood_enrichment', '') if isinstance(tool_summary, Mapping) else ''}",
            f"- Interaction summary: {json.dumps(tool_summary.get('nhood_enrichment_summary', {}), ensure_ascii=True)}",
            "",
            "## Reviewer",
            f"- Output: {json.dumps(review_output, ensure_ascii=True)}",
        ]
    )
    if summary.get("contract_violations"):
        lines.extend(["", "## Contract Violations"])
        for item in summary["contract_violations"]:
            lines.append(f"- {item['node_id']}: {item['message']}")
    return "\n".join(lines) + "\n"


def render_squidpy_case_study_narrative(summary: Mapping[str, Any]) -> str:
    interaction_case = bool(summary.get("interaction_case", False))
    dataset_label = str(summary.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    review_output = summary.get("review_output", {})
    selected_repo = tool_output.get("selected_repo", "") if isinstance(tool_output, Mapping) else ""
    cluster_count = tool_summary.get("cluster_count", "") if isinstance(tool_summary, Mapping) else ""
    interaction_summary = tool_summary.get("nhood_enrichment_summary", {}) if isinstance(tool_summary, Mapping) else {}
    return "\n".join(
        [
            f"# Squidpy {dataset_label} Experiment 3 Narrative",
            "",
            (
                "This run used a sandboxed Squidpy environment cloned from an external candidate repository, "
                f"wrapped as an MCP tool, to run a {dataset_label} neighborhood-enrichment analysis and emit both visual and structured interaction outputs."
                if interaction_case
                else "This run used a sandboxed Squidpy environment cloned from an external candidate repository, "
                f"wrapped as an MCP tool, to reproduce a {dataset_label} spatial plot from a methods excerpt and target figure description."
            ),
            "",
            f"Planner choice: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
            (
                f"Refiner output: {json.dumps(summary.get('refiner_output', {}), ensure_ascii=True)}"
                if summary.get("refiner_output")
                else "Refiner output: not used"
            ),
            (
                "Sandboxed tool result: "
                f"repo={selected_repo}, clusters={cluster_count}, figure={summary.get('generated_figure_path', '')}, "
                f"interaction_figure={summary.get('generated_interaction_figure_path', '')}"
            ),
            f"Interaction summary: {json.dumps(interaction_summary, ensure_ascii=True)}",
            f"Reviewer assessment: {json.dumps(review_output, ensure_ascii=True)}",
            f"Hard-check status: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
            "",
            f"Final status: success={summary.get('success', False)} failure_type={summary.get('failure_type', 'none')}",
        ]
    ) + "\n"


def _summarize_visium_multi_agent_collaboration_case_study(
    report: ExecutionReport,
    *,
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
) -> dict[str, Any]:
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    planner_output = _preferred_case_node_outputs(report, "planner")
    cluster_payload = _preferred_case_node_outputs(report, "scanpy_cluster_tool").get("result", {})
    interaction_payload = _preferred_case_node_outputs(report, "squidpy_interaction_tool").get("result", {})
    reviewer_output = _preferred_case_node_outputs(report, "bio_reviewer")
    cluster_summary = cluster_payload.get("summary", {}) if isinstance(cluster_payload, Mapping) else {}
    interaction_summary = interaction_payload.get("summary", {}) if isinstance(interaction_payload, Mapping) else {}
    return {
        "benchmark": "case_study",
        "case_id": case_assets["case_id"],
        "dataset_id": case_assets.get("dataset_id", ""),
        "dataset_label": dataset_label,
        "success": report.success,
        "failure_type": report.failure_type.value,
        "summary": report.summary,
        "workflow_signature": _workflow_signature(report),
        "confidence": report.confidence,
        "cost": report.cost.model_dump(),
        "hard_checks": [item.model_dump(mode="json") for item in report.hard_checks],
        "contract_violations": [item.model_dump(mode="json") for item in report.contract_violations],
        "blame_candidates": [item.model_dump(mode="json") for item in report.blame_candidates],
        "planner_output": planner_output,
        "cluster_tool_output": cluster_payload,
        "interaction_tool_output": interaction_payload,
        "review_output": reviewer_output,
        "generated_cluster_figure_path": cluster_summary.get("cluster_figure_path", ""),
        "generated_marker_figure_path": cluster_summary.get("marker_figure_path", ""),
        "generated_cluster_summary_path": cluster_summary.get("summary_path", ""),
        "generated_annotated_data_path": cluster_summary.get("annotated_data_path", ""),
        "generated_spatial_figure_path": interaction_summary.get("figure_path", ""),
        "generated_interaction_figure_path": interaction_summary.get("interaction_figure_path", ""),
        "generated_interaction_summary_path": interaction_summary.get("summary_path", ""),
        "reference_summary_path": reference_assets["reference_summary_path"],
        "reference_cluster_figure_path": reference_assets["reference_cluster_figure_path"],
        "reference_marker_figure_path": reference_assets["reference_marker_figure_path"],
        "reference_spatial_figure_path": reference_assets["reference_spatial_figure_path"],
        "reference_interaction_figure_path": reference_assets["reference_interaction_figure_path"],
    }


def render_visium_multi_agent_collaboration_analysis(summary: Mapping[str, Any]) -> str:
    dataset_label = str(summary.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    cluster_output = summary.get("cluster_tool_output", {})
    interaction_output = summary.get("interaction_tool_output", {})
    cluster_summary = cluster_output.get("summary", {}) if isinstance(cluster_output, Mapping) else {}
    interaction_summary = interaction_output.get("summary", {}) if isinstance(interaction_output, Mapping) else {}
    return "\n".join(
        [
            f"# {dataset_label} multi-agent collaboration analysis",
            "",
            f"- Success: {summary.get('success', False)}",
            f"- Failure type: {summary.get('failure_type', 'none')}",
            f"- Workflow signature: {summary.get('workflow_signature', '')}",
            f"- Cost: {json.dumps(summary.get('cost', {}), ensure_ascii=True)}",
            f"- Cluster repo: {cluster_output.get('selected_repo', '') if isinstance(cluster_output, Mapping) else ''}",
            f"- Interaction repo: {interaction_output.get('selected_repo', '') if isinstance(interaction_output, Mapping) else ''}",
            f"- Cluster count: {cluster_summary.get('cluster_count', '') if isinstance(cluster_summary, Mapping) else ''}",
            f"- Marker gene count: {cluster_summary.get('marker_gene_count', '') if isinstance(cluster_summary, Mapping) else ''}",
            f"- Interaction summary: {json.dumps(interaction_summary.get('nhood_enrichment_summary', {}), ensure_ascii=True)}",
            f"- Hard checks: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
            f"- Reviewer: {json.dumps(summary.get('review_output', {}), ensure_ascii=True)}",
        ]
    ) + "\n"


def render_visium_multi_agent_collaboration_narrative(summary: Mapping[str, Any]) -> str:
    dataset_label = str(summary.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    cluster_output = summary.get("cluster_tool_output", {})
    interaction_output = summary.get("interaction_tool_output", {})
    return "\n".join(
        [
            f"# {dataset_label} multi-agent collaboration narrative",
            "",
            f"This run used two sandboxed specialists on the same public {dataset_label} dataset: a Scanpy clustering/marker agent and a Squidpy spatial-interaction agent.",
            f"Planner choice: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
            f"Cluster specialist output: {json.dumps(cluster_output, ensure_ascii=True)}",
            f"Interaction specialist output: {json.dumps(interaction_output, ensure_ascii=True)}",
            f"Reviewer assessment: {json.dumps(summary.get('review_output', {}), ensure_ascii=True)}",
            f"Hard-check status: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
        ]
    ) + "\n"


def _summarize_visium_multi_agent_monolith_case_study(
    report: ExecutionReport,
    *,
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
) -> dict[str, Any]:
    dataset_label = str(case_assets.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    planner_output = _preferred_case_node_outputs(report, "planner")
    tool_payload = _preferred_case_node_outputs(report, "visium_monolith_tool").get("result", {})
    reviewer_output = _preferred_case_node_outputs(report, "bio_reviewer")
    tool_summary = tool_payload.get("summary", {}) if isinstance(tool_payload, Mapping) else {}
    cluster_summary = tool_summary.get("cluster_summary", {}) if isinstance(tool_summary, Mapping) else {}
    interaction_summary = tool_summary.get("interaction_summary", {}) if isinstance(tool_summary, Mapping) else {}
    return {
        "benchmark": "case_study",
        "case_id": case_assets["case_id"],
        "dataset_id": case_assets.get("dataset_id", ""),
        "dataset_label": dataset_label,
        "success": report.success,
        "failure_type": report.failure_type.value,
        "summary": report.summary,
        "workflow_signature": _workflow_signature(report),
        "confidence": report.confidence,
        "cost": report.cost.model_dump(),
        "hard_checks": [item.model_dump(mode="json") for item in report.hard_checks],
        "contract_violations": [item.model_dump(mode="json") for item in report.contract_violations],
        "blame_candidates": [item.model_dump(mode="json") for item in report.blame_candidates],
        "planner_output": planner_output,
        "tool_output": tool_payload,
        "review_output": reviewer_output,
        "generated_cluster_figure_path": cluster_summary.get("cluster_figure_path", ""),
        "generated_marker_figure_path": cluster_summary.get("marker_figure_path", ""),
        "generated_cluster_summary_path": cluster_summary.get("summary_path", ""),
        "generated_spatial_figure_path": interaction_summary.get("figure_path", ""),
        "generated_interaction_figure_path": interaction_summary.get("interaction_figure_path", ""),
        "generated_interaction_summary_path": interaction_summary.get("summary_path", ""),
        "reference_summary_path": reference_assets["reference_summary_path"],
    }


def render_visium_multi_agent_monolith_analysis(summary: Mapping[str, Any]) -> str:
    dataset_label = str(summary.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    return "\n".join(
        [
            f"# {dataset_label} multi-agent monolith analysis",
            "",
            f"- Success: {summary.get('success', False)}",
            f"- Workflow signature: {summary.get('workflow_signature', '')}",
            f"- Cost: {json.dumps(summary.get('cost', {}), ensure_ascii=True)}",
            f"- Hard checks: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
            f"- Reviewer: {json.dumps(summary.get('review_output', {}), ensure_ascii=True)}",
        ]
    ) + "\n"


def render_visium_multi_agent_monolith_narrative(summary: Mapping[str, Any]) -> str:
    dataset_label = str(summary.get("dataset_label", "Spatial dataset")).strip() or "Spatial dataset"
    return "\n".join(
        [
            f"# {dataset_label} multi-agent monolith narrative",
            "",
            f"This run used one sandboxed monolith baseline to execute both clustering and spatial interaction analysis inside a single environment for the public {dataset_label} task.",
            f"Planner choice: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
            f"Tool output: {json.dumps(summary.get('tool_output', {}), ensure_ascii=True)}",
            f"Reviewer assessment: {json.dumps(summary.get('review_output', {}), ensure_ascii=True)}",
        ]
    ) + "\n"


def _artifact_mime(path: str) -> Optional[str]:
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".json":
        return "application/json"
    if suffix == ".h5ad":
        return "application/x-hdf5"
    return None


def _render_stage0_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Stage 0 validation",
        "",
        f"- Success: {summary.get('success', False)}",
        f"- API probe: {json.dumps(summary.get('api_probe', {}), ensure_ascii=True)}",
        f"- Capabilities: {json.dumps(summary.get('capabilities', {}), ensure_ascii=True)}",
    ]
    if "math_manifest" in summary:
        lines.append(f"- MATH records prepared: {summary['math_manifest'].get('record_count', 0)}")
    if "math_error" in summary:
        lines.append(f"- MATH error: {summary['math_error']}")
    if "humaneval_manifest" in summary:
        lines.append(f"- HumanEval records prepared: {summary['humaneval_manifest'].get('record_count', 0)}")
    if "humaneval_error" in summary:
        lines.append(f"- HumanEval error: {summary['humaneval_error']}")
    return "\n".join(lines) + "\n"


def _run_blueprint(
    config: Mapping[str, Any],
    blueprint: WorkflowBlueprint,
    executor: Any,
    *,
    handlers: Optional[Mapping[str, Any]] = None,
) -> tuple[WorkflowBlueprint, ExecutionReport]:
    executor_cfg = _executor_settings(config)
    if executor_cfg.get("repair_enabled", False):
        return executor.execute_with_repair(
            blueprint,
            handlers=handlers,
            max_iterations=int(executor_cfg.get("max_repair_iterations", 3)),
        )
    return blueprint, executor.execute_blueprint(blueprint, handlers=handlers)


def _build_generic_case_study_blueprint(
    config: Mapping[str, Any],
    *,
    benchmark_settings: Mapping[str, Any],
    case_assets: Mapping[str, Any],
    recorder: RunRecorder,
) -> WorkflowBlueprint:
    blueprint = build_blueprint_from_config(config)
    output_dir = recorder.artifacts_dir / "case_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    merged_hints = dict(blueprint.task.hints)
    merged_hints.update(
        {
            "case_id": case_assets.get("case_id", ""),
            "case_title": str(benchmark_settings.get("title", blueprint.task.title)),
            "methods_excerpt": case_assets.get("methods_excerpt", ""),
            "target_figure_description": case_assets.get("target_figure_description", ""),
            "task_notes": case_assets.get("task_notes", ""),
            "candidate_repos": list(case_assets.get("candidate_repos", [])),
            "case_root": case_assets.get("case_root", ""),
            "repo_root": case_assets.get("repo_root", ""),
            "generated_dir": case_assets.get("generated_dir", ""),
            "data_dir": case_assets.get("data_dir", ""),
            "reference_dir": case_assets.get("reference_dir", ""),
            "input_assets": dict(case_assets.get("input_assets", {})),
            "expected_outputs": list(case_assets.get("expected_outputs", [])),
            "output_contract": dict(case_assets.get("output_contract", {})),
            "output_dir": str(output_dir),
            "run_dir": str(recorder.root),
        }
    )
    task = blueprint.task.model_copy(
        update={
            "task_id": str(blueprint.task.task_id or case_assets.get("case_id", "case_study")),
            "title": str(benchmark_settings.get("title", blueprint.task.title or case_assets.get("case_id", "Case Study"))),
            "description": str(
                benchmark_settings.get("target_figure_description")
                or blueprint.task.description
                or case_assets.get("target_figure_description", "")
                or case_assets.get("methods_excerpt", "")
            ),
            "hints": merged_hints,
        }
    )
    meta = dict(blueprint.meta)
    meta.update(
        {
            "case_id": case_assets.get("case_id", ""),
            "case_runner_mode": "compiled_generic",
            "case_asset_manifest": {
                "candidate_repo_count": len(case_assets.get("candidate_repos", [])),
                "expected_output_count": len(case_assets.get("expected_outputs", [])),
            },
        }
    )
    return blueprint.model_copy(update={"task": task, "meta": meta})


def _summarize_generic_case_study(
    report: ExecutionReport,
    *,
    blueprint: WorkflowBlueprint,
    case_assets: Mapping[str, Any],
) -> dict[str, Any]:
    compile_trace = next(
        (dict(event.get("payload", {})) for event in report.events if event.get("type") == "compile_trace"),
        dict(blueprint.meta.get("compile_trace", {})),
    )
    selected_repos, selected_specialists = _extract_report_selections(report)
    runtime_events = [dict(event) for event in report.events]
    trace_details = []
    for trace in report.traces:
        trace_details.append(
            {
                "node_id": trace.node_id,
                "role": trace.role,
                "status": trace.status,
                "confidence": trace.confidence,
                "failure_type": trace.failure_type.value if trace.failure_type else "none",
                "error": trace.error,
                "inputs": trace.inputs,
                "outputs": trace.outputs,
                "trace": trace.trace,
                "artifacts": [_artifact_path_from_uri(getattr(item, "uri", "")) for item in trace.artifacts],
            }
        )
    routing_eval: dict[str, Any] = {}
    expected_routed_objective = str(case_assets.get("hidden_assets", {}).get("expected_routed_objective", "")).strip()
    if expected_routed_objective:
        predicted_routed_objective = ""
        for trace in report.traces:
            outputs = trace.outputs if isinstance(trace.outputs, Mapping) else {}
            routed_objective = outputs.get("routed_objective")
            if isinstance(routed_objective, str) and routed_objective.strip():
                predicted_routed_objective = routed_objective.strip()
                break
        if predicted_routed_objective:
            routing_eval = {
                "expected_routed_objective": expected_routed_objective,
                "predicted_routed_objective": predicted_routed_objective,
                "correct": predicted_routed_objective == expected_routed_objective,
            }
    return {
        "benchmark": "case_study",
        "case_id": case_assets.get("case_id", ""),
        "title": blueprint.task.title,
        "success": report.success,
        "failure_type": report.failure_type.value,
        "summary": report.summary,
        "workflow_signature": _workflow_signature(report),
        "workflow_pattern": str(blueprint.meta.get("workflow_pattern", "")),
        "compile_trace": compile_trace,
        "graph_design": _blueprint_structure_summary(blueprint),
        "input_assets": dict(case_assets.get("input_assets", {})),
        "hidden_assets": dict(case_assets.get("hidden_assets", {})),
        "expected_outputs": list(case_assets.get("expected_outputs", [])),
        "output_contract": dict(case_assets.get("output_contract", {})),
        "methods_excerpt": str(case_assets.get("methods_excerpt", "")),
        "target_figure_description": str(case_assets.get("target_figure_description", "")),
        "candidate_repos": list(case_assets.get("candidate_repos", [])),
        "selected_repos": selected_repos,
        "selected_specialists": selected_specialists,
        "routing_eval": routing_eval,
        "runtime_events": runtime_events,
        "trace_details": trace_details,
        "artifact_paths": _report_artifact_paths(report),
        "activated_gates": list(report.activated_gates),
        "active_subgraphs": list(report.active_subgraphs),
        "hard_checks": [item.model_dump(mode="json") for item in report.hard_checks],
        "soft_judges": [item.model_dump(mode="json") for item in report.soft_judges],
        "contract_violations": [item.model_dump(mode="json") for item in report.contract_violations],
        "cost": report.cost.model_dump(),
        "confidence": report.confidence,
    }


def _blueprint_structure_summary(blueprint: WorkflowBlueprint) -> dict[str, Any]:
    return {
        "task_id": blueprint.task.task_id,
        "base_nodes": [
            {
                "node_id": node.node_id,
                "role": node.role,
                "kind": node.kind.value,
                "has_tool_discovery": bool(node.tool_discovery and node.tool_discovery.mode.value != "disabled"),
                "direct_sandbox": bool(node.meta.get("direct_sandbox_handler", False)),
            }
            for node in blueprint.base_nodes
        ],
        "base_edges": [edge.model_dump(mode="json") for edge in blueprint.base_edges],
        "subgraphs": [
            {
                "subgraph_id": subgraph.subgraph_id,
                "purpose": subgraph.purpose,
                "default_enabled": subgraph.default_enabled,
                "nodes": [node.node_id for node in subgraph.nodes],
            }
            for subgraph in blueprint.subgraphs
        ],
        "gates": [gate.model_dump(mode="json") for gate in blueprint.gates],
    }


def _generic_specialist_router_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    task = inputs.get("task", {}) if isinstance(inputs.get("task", {}), Mapping) else {}
    hints = task.get("hints", {}) if isinstance(task.get("hints", {}), Mapping) else {}
    candidate_repos = hints.get("candidate_repos", []) if isinstance(hints.get("candidate_repos", []), list) else []
    repo_names = [str(repo.get("name", "")).strip() for repo in candidate_repos if isinstance(repo, Mapping)]

    selected: list[str] = []
    preferred_order = ("SpatialAgent", "BioDiscoveryAgent")
    for preferred in preferred_order:
        for name in repo_names:
            if name.lower() == preferred.lower() and preferred not in selected:
                selected.append(name)
    for name in repo_names:
        if name and name not in selected:
            selected.append(name)
    if not selected:
        selected = ["specialist_a", "specialist_b"]

    handoff_contract = {
        "specialist_a_delivers": [
            "spatial_context_report_path",
            "routed_objective_path",
            "routed_objective",
            "objective_scores",
        ],
        "specialist_b_receives": [
            "routed_objective",
            "spatial_context_report_path",
        ],
        "integrator_requires": [
            "spatial_context_report_path",
            "ranked_gene_batch_path",
            "hitrate_eval_path",
        ],
        "review_focus": [
            "objective-grounding",
            "specialist-handoff-consistency",
            "artifact-completeness",
        ],
    }
    outputs = {
        "selected_specialists": selected,
        "handoff_contract": handoff_contract,
        "summary": f"Selected specialists {selected} with a fixed spatial-to-perturbation handoff contract.",
        "requires_review": True,
    }
    return NodeExecutionResult(
        outputs=outputs,
        trace={
            "deterministic_router": True,
            "selected_specialists": selected,
            "candidate_repo_names": repo_names,
            "handoff_contract": handoff_contract,
        },
        confidence=0.9,
    )


def _extract_report_selections(report: ExecutionReport) -> tuple[dict[str, str], dict[str, list[str]]]:
    selected_repos: dict[str, str] = {}
    selected_specialists: dict[str, list[str]] = {}
    for trace in report.traces:
        outputs = trace.outputs if isinstance(trace.outputs, Mapping) else {}
        if isinstance(outputs.get("selected_repo"), str) and str(outputs.get("selected_repo", "")).strip():
            selected_repos[trace.node_id] = str(outputs["selected_repo"]).strip()
        if isinstance(trace.trace, Mapping) and isinstance(trace.trace.get("selected_repo"), str):
            repo_name = str(trace.trace.get("selected_repo", "")).strip()
            if repo_name:
                selected_repos.setdefault(trace.node_id, repo_name)
        specialists = outputs.get("selected_specialists")
        if isinstance(specialists, list):
            selected_specialists[trace.node_id] = [str(item) for item in specialists]
        elif isinstance(trace.trace, Mapping) and isinstance(trace.trace.get("selected_specialists"), list):
            selected_specialists[trace.node_id] = [str(item) for item in trace.trace.get("selected_specialists", [])]
    return selected_repos, selected_specialists


def _report_artifact_paths(report: ExecutionReport) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for trace in report.traces:
        for artifact in trace.artifacts:
            path = _artifact_path_from_uri(getattr(artifact, "uri", ""))
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _artifact_path_from_uri(uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("file://"):
        return uri.removeprefix("file://")
    return uri


def render_generic_case_study_analysis(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# {summary.get('title', 'case study')}",
        "",
        "## Task",
        f"- Case ID: `{summary.get('case_id', '')}`",
        f"- Success: `{summary.get('success', False)}`",
        f"- Failure type: `{summary.get('failure_type', 'none')}`",
        f"- Workflow signature: `{summary.get('workflow_signature', '')}`",
        f"- Workflow pattern: `{summary.get('workflow_pattern', '')}`",
        "",
        "## Inputs",
        f"- Input assets: `{json.dumps(summary.get('input_assets', {}), ensure_ascii=True)}`",
        f"- Hidden assets: `{json.dumps(summary.get('hidden_assets', {}), ensure_ascii=True)}`",
        f"- Expected outputs: `{json.dumps(summary.get('expected_outputs', []), ensure_ascii=True)}`",
        "",
        "## Workflow Design",
        f"- Graph design: `{json.dumps(summary.get('graph_design', {}), ensure_ascii=True)}`",
        f"- Compile trace: `{json.dumps(summary.get('compile_trace', {}), ensure_ascii=True)}`",
        "",
        "## Search / Selection",
        f"- Candidate repos: `{json.dumps(summary.get('candidate_repos', []), ensure_ascii=True)}`",
        f"- Selected repos: `{json.dumps(summary.get('selected_repos', {}), ensure_ascii=True)}`",
        f"- Selected specialists: `{json.dumps(summary.get('selected_specialists', {}), ensure_ascii=True)}`",
        f"- Routing evaluation: `{json.dumps(summary.get('routing_eval', {}), ensure_ascii=True)}`",
        "",
        "## Runtime",
        f"- Activated gates: `{json.dumps(summary.get('activated_gates', []), ensure_ascii=True)}`",
        f"- Active subgraphs: `{json.dumps(summary.get('active_subgraphs', []), ensure_ascii=True)}`",
        f"- Cost: `{json.dumps(summary.get('cost', {}), ensure_ascii=True)}`",
        "",
        "## Node Trace",
    ]
    trace_details = summary.get("trace_details", [])
    if not isinstance(trace_details, Sequence) or not trace_details:
        lines.append("- None")
    else:
        for trace in trace_details:
            if not isinstance(trace, Mapping):
                continue
            lines.append(
                f"- `{trace.get('node_id', '')}` ({trace.get('role', '')}): "
                f"status={trace.get('status', '')} confidence={trace.get('confidence', 0.0)} "
                f"failure={trace.get('failure_type', 'none')}"
            )
            lines.append(f"  outputs: `{json.dumps(trace.get('outputs', {}), ensure_ascii=True)}`")
            lines.append(f"  trace: `{json.dumps(trace.get('trace', {}), ensure_ascii=True)}`")
        lines.append("")
    lines.extend(
        [
            "## Artifacts",
            f"- Artifact paths: `{json.dumps(summary.get('artifact_paths', []), ensure_ascii=True)}`",
            "",
            "## Evaluation",
            f"- Hard checks: `{json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}`",
            f"- Soft judges: `{json.dumps(summary.get('soft_judges', []), ensure_ascii=True)}`",
            f"- Contract violations: `{json.dumps(summary.get('contract_violations', []), ensure_ascii=True)}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def render_generic_case_study_narrative(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# {summary.get('title', 'Case Study')}",
        "",
        f"The compiled workflow selected pattern `{summary.get('workflow_pattern', '')}` and executed "
        f"`{summary.get('workflow_signature', '')}`.",
        "",
        f"Selected repos: `{json.dumps(summary.get('selected_repos', {}), ensure_ascii=True)}`",
        f"Selected specialists: `{json.dumps(summary.get('selected_specialists', {}), ensure_ascii=True)}`",
        f"Routing evaluation: `{json.dumps(summary.get('routing_eval', {}), ensure_ascii=True)}`",
        "",
        f"Outcome: success=`{summary.get('success', False)}`, failure_type=`{summary.get('failure_type', 'none')}`.",
    ]
    return "\n".join(lines) + "\n"


def _executor_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    executor_cfg = dict(config.get("executor", {}))
    experiment_cfg = config.get("experiment")
    if isinstance(experiment_cfg, Mapping):
        nested_executor = experiment_cfg.get("executor")
        if isinstance(nested_executor, Mapping):
            executor_cfg = {**executor_cfg, **dict(nested_executor)}
    return executor_cfg


def _config_to_yaml(config: DictConfig | Mapping[str, Any]) -> str:
    if isinstance(config, DictConfig):
        return OmegaConf.to_yaml(config, resolve=True)
    return json.dumps(resolve_hydra_config(config), ensure_ascii=True, indent=2)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            records.append(json.loads(stripped))
    return records


def _load_indices(manifest: Mapping[str, Any], *, subset: str) -> list[int]:
    path_key = f"{subset}_indices_path"
    if path_key not in manifest:
        raise BenchmarkSetupError(f"Unknown subset '{subset}' for manifest")
    return json.loads(Path(str(manifest[path_key])).read_text(encoding="utf-8"))


def _workflow_signature(report: ExecutionReport) -> str:
    nodes = [trace.node_id for trace in report.traces if trace.status in {"success", "cached"}]
    return "->".join(nodes)


def _planner_question_type_from_report(report: ExecutionReport) -> str:
    for trace in report.traces:
        if trace.node_id not in {"planner", "solver"}:
            continue
        outputs = trace.outputs or {}
        question_type = outputs.get("question_type")
        if isinstance(question_type, str) and question_type.strip():
            return question_type.strip()
    return "unknown"


def _cost_value(result: Mapping[str, Any], key: str) -> float:
    cost = result.get("cost", {})
    if not isinstance(cost, Mapping):
        return 0.0
    try:
        return float(cost.get(key, 0.0) or 0.0)
    except Exception:
        return 0.0


def _trace_role_and_node_counts(result: Mapping[str, Any]) -> tuple[Counter[str], Counter[str]]:
    role_counter: Counter[str] = Counter()
    node_counter: Counter[str] = Counter()
    report_path = result.get("report_path")
    if not isinstance(report_path, str) or not report_path:
        return role_counter, node_counter
    path = Path(report_path)
    if not path.exists():
        return role_counter, node_counter
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return role_counter, node_counter
    for trace in payload.get("traces", []):
        if trace.get("status") not in {"success", "cached"}:
            continue
        node_id = str(trace.get("node_id", "") or "")
        role = str(trace.get("role", "") or "")
        if node_id:
            node_counter[node_id] += 1
        if role:
            role_counter[role] += 1
    return role_counter, node_counter


def render_repeat_benchmark_analysis(summary: Mapping[str, Any], metric_name: str) -> str:
    lines = [
        f"# {summary['benchmark']} repeat aggregate",
        "",
        f"- Reporting protocol: {summary.get('reporting_protocol', 'unknown')}",
        f"- Repeat count: {summary.get('repeat_count', 0)}",
        f"- Subset: {summary.get('subset', '')}",
        f"- Model: {summary.get('model_name', '')}",
        f"- Task count: {summary.get('task_count', 0)}",
        f"- {metric_name}: {float(summary.get(metric_name, 0.0)):.4f} +/- {float(summary.get(f'{metric_name}_std', 0.0)):.4f}",
        f"- Average USD: {float(summary.get('average_usd', 0.0)):.6f} +/- {float(summary.get('average_usd_std', 0.0)):.6f}",
        f"- Average input tokens: {float(summary.get('average_input_tokens', 0.0)):.2f} +/- {float(summary.get('average_input_tokens_std', 0.0)):.2f}",
        f"- Average output tokens: {float(summary.get('average_output_tokens', 0.0)):.2f} +/- {float(summary.get('average_output_tokens_std', 0.0)):.2f}",
        f"- Average latency: {float(summary.get('average_latency_s', 0.0)):.2f}s +/- {float(summary.get('average_latency_s_std', 0.0)):.2f}s",
        "",
        "## Per repeat",
    ]
    for item in summary.get("per_repeat", []):
        lines.append(
            f"- `{item.get('run_dir', '')}`: "
            f"{metric_name}={float(item.get(metric_name, 0.0)):.4f} "
            f"average_usd={float(item.get('average_usd', 0.0)):.6f} "
            f"subset={item.get('subset', '')}"
        )
    return "\n".join(lines) + "\n"


def _mean(values: Sequence[float] | Any) -> float:
    data = [float(value) for value in values]
    if not data:
        return 0.0
    return float(statistics.fmean(data))


def _stddev(values: Sequence[float] | Any) -> float:
    data = [float(value) for value in values]
    if len(data) <= 1:
        return 0.0
    return float(statistics.pstdev(data))


def _safe_ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _slugify(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part) or "run"


# ---------------------------------------------------------------------------
# GSM8K, MBPP, HotpotQA, DROP benchmark support
# ---------------------------------------------------------------------------


def build_gsm8k_task_blueprint(
    base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]
) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints["benchmark"] = "gsm8k"
    updated_task = base_blueprint.task.model_copy(
        update={
            "task_id": f"{base_blueprint.task.task_id}:{sample['id']}",
            "description": sample["prompt"],
            "hints": task_hints,
        }
    )
    updated_meta = dict(base_blueprint.meta)
    updated_meta["sample_id"] = sample["id"]
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


def build_mbpp_task_blueprint(
    base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]
) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints.update({
        "benchmark": "mbpp",
        "entry_point": sample.get("entry_point", ""),
    })
    prompt_text = sample.get("prompt", "")
    if sample.get("test_list"):
        prompt_text += "\n\nExample tests:\n" + "\n".join(sample["test_list"][:3])
    updated_task = base_blueprint.task.model_copy(
        update={
            "task_id": f"{base_blueprint.task.task_id}:{sample['id']}",
            "description": prompt_text,
            "hints": task_hints,
        }
    )
    updated_meta = dict(base_blueprint.meta)
    updated_meta.update({"sample_id": sample["id"], "entry_point": sample.get("entry_point", "")})
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


def build_hotpotqa_task_blueprint(
    base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]
) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints.update({
        "benchmark": "hotpotqa",
        "question_type": sample.get("type", ""),
        "difficulty_level": sample.get("level", ""),
    })
    # Include context paragraphs in the prompt
    context_text = ""
    raw_context = sample.get("context", [])
    if isinstance(raw_context, list):
        for ctx_item in raw_context:
            if isinstance(ctx_item, list) and len(ctx_item) >= 2:
                title = ctx_item[0]
                sentences = ctx_item[1] if isinstance(ctx_item[1], list) else [ctx_item[1]]
                context_text += f"\n{title}: {''.join(str(s) for s in sentences)}"
    prompt = f"Answer the following question based on the provided context.\n\nContext:{context_text}\n\nQuestion: {sample['question']}\n\nProvide the answer only."
    updated_task = base_blueprint.task.model_copy(
        update={
            "task_id": f"{base_blueprint.task.task_id}:{sample['id']}",
            "description": prompt,
            "hints": task_hints,
        }
    )
    updated_meta = dict(base_blueprint.meta)
    updated_meta["sample_id"] = sample["id"]
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


def build_drop_task_blueprint(
    base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]
) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints["benchmark"] = "drop"
    prompt = f"{sample['question']}\n\nProvide the answer only."
    updated_task = base_blueprint.task.model_copy(
        update={
            "task_id": f"{base_blueprint.task.task_id}:{sample['id']}",
            "description": prompt,
            "hints": task_hints,
        }
    )
    updated_meta = dict(base_blueprint.meta)
    updated_meta["sample_id"] = sample["id"]
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


def _build_gsm8k_task_handlers(
    config: Mapping[str, Any],
    blueprint: WorkflowBlueprint,
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    """GSM8K uses the same handlers as MATH (program_exec + selector)."""
    return _build_math_task_handlers(config, blueprint, sample)


def _build_mbpp_task_handlers(
    config: Mapping[str, Any],
    blueprint: WorkflowBlueprint,
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    """MBPP uses the same handlers as HumanEval (test runner)."""
    return _build_humaneval_task_handlers(config, blueprint, sample)


def _build_gsm8k_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    """Build task result for GSM8K — same grading as MATH (exact answer match)."""
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    prediction = str(answer_payload.get("final_answer", answer_payload.get("result", "")) or "").strip()
    gold = str(sample.get("gold_answer", "")).strip()
    correct = _gsm8k_equal(prediction, gold)
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample["question"][:200],
        "metadata": {"source_config": sample.get("source_config", "")},
        "prediction_node": answer_node,
        "prediction_answer": prediction,
        "gold_answer": gold,
        "correct": correct,
        "workflow_signature": _workflow_signature(report),
        "activated_gates": report.activated_gates,
        "active_subgraphs": report.active_subgraphs,
        "activated_node_count": sum(1 for t in report.traces if t.status in {"success", "cached"}),
        "activated_subgraph_count": len(report.active_subgraphs),
        "runtime_failure_type": report.failure_type.value,
        "failure_type": report.failure_type.value if report.failure_type.value != "none" else ("incorrect_answer" if not correct else "none"),
        "confidence": report.confidence,
        "summary": report.summary,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _gsm8k_equal(prediction: str, reference: str) -> bool:
    """Compare GSM8K answers — extract final number from both."""
    pred_num = _extract_gsm8k_number(prediction)
    ref_num = _extract_gsm8k_number(reference)
    if pred_num is not None and ref_num is not None:
        return abs(pred_num - ref_num) < 1e-3
    return prediction.strip() == reference.strip()


def _extract_gsm8k_number(text: str) -> float | None:
    """Extract the final number from a GSM8K answer string."""
    text = text.strip().replace(",", "").replace("$", "").replace("%", "")
    # Try direct float parse
    try:
        return float(text)
    except ValueError:
        pass
    # Extract last number from text
    numbers = re.findall(r"-?\d+\.?\d*", text)
    if numbers:
        try:
            return float(numbers[-1])
        except ValueError:
            pass
    return None


def _build_hotpotqa_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    raw_prediction = str(answer_payload.get("final_answer", answer_payload.get("result", "")) or "").strip()
    prediction = _strip_answer_verbosity(raw_prediction)
    gold = str(sample.get("gold_answer", "")).strip()
    f1 = _compute_f1(prediction, gold)
    em = _compute_exact_match(prediction, gold)
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample["question"][:200],
        "metadata": {"type": sample.get("type", ""), "level": sample.get("level", "")},
        "prediction_node": answer_node,
        "prediction_answer": prediction,
        "gold_answer": gold,
        "correct": em or f1 >= 0.8,
        "f1": f1,
        "exact_match": em,
        "workflow_signature": _workflow_signature(report),
        "activated_node_count": sum(1 for t in report.traces if t.status in {"success", "cached"}),
        "failure_type": report.failure_type.value if report.failure_type.value != "none" else ("incorrect_answer" if f1 < 0.5 else "none"),
        "confidence": report.confidence,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _build_drop_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    raw_prediction = str(answer_payload.get("final_answer", answer_payload.get("result", "")) or "").strip()
    prediction = _strip_answer_verbosity(raw_prediction)
    gold = str(sample.get("gold_answer", "")).strip()
    f1 = _compute_f1(prediction, gold)
    em = _compute_exact_match(prediction, gold)
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample["question"][:200],
        "metadata": {},
        "prediction_node": answer_node,
        "prediction_answer": prediction,
        "gold_answer": gold,
        "correct": em or f1 >= 0.8,
        "f1": f1,
        "exact_match": em,
        "workflow_signature": _workflow_signature(report),
        "activated_node_count": sum(1 for t in report.traces if t.status in {"success", "cached"}),
        "failure_type": report.failure_type.value if report.failure_type.value != "none" else ("incorrect_answer" if f1 < 0.5 else "none"),
        "confidence": report.confidence,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _build_mbpp_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    """Build task result for MBPP — uses MBPP-specific grading (check() with no args)."""
    from agentcoop.benchmarks import grade_mbpp_prediction
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    grading = grade_mbpp_prediction(answer_payload, sample)
    public_test_outputs = report.node_results.get("public_test_runner", NodeExecutionResult()).outputs
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question": sample["question"][:200],
        "metadata": {"entry_point": sample.get("entry_point", "")},
        "prediction_node": answer_node,
        **grading,
        "public_test_passed": bool(public_test_outputs.get("passed", False))
        if isinstance(public_test_outputs, Mapping) else False,
        "workflow_signature": _workflow_signature(report),
        "activated_node_count": sum(1 for t in report.traces if t.status in {"success", "cached"}),
        "failure_type": report.failure_type.value if report.failure_type.value != "none" else ("incorrect_answer" if not grading["correct"] else "none"),
        "confidence": report.confidence,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _strip_answer_verbosity(answer: str) -> str:
    """Post-process an LLM answer to remove common verbosity patterns.

    This is a general-purpose cleanup applied before grading for QA-style
    benchmarks (HotpotQA, DROP). It strips:
    - Leading "The answer is" / "Answer:" prefixes
    - Trailing periods
    - Surrounding quotes
    - Common unit suffixes when the answer is clearly numeric
    """
    if not answer:
        return ""
    s = answer.strip()
    # Strip common prefixes
    for prefix in [
        "The answer is ", "Answer: ", "Answer is ", "The final answer is ",
        "Final answer: ", "answer: ", "answer is ", "It is ", "It's ",
    ]:
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):]
    # Strip surrounding quotes
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1]
    # Strip trailing period (but keep if part of abbreviation like "Dr.")
    if s.endswith(".") and len(s) > 2 and s[-3] not in " ":
        pass  # likely abbreviation
    elif s.endswith("."):
        s = s[:-1]
    # Strip common unit suffixes for numeric answers
    import re as _re
    num_match = _re.match(r"^(-?\d+(?:\.\d+)?)\s*(%|percent|years?|days?|hours?|minutes?|dollars?|\$)$", s.strip(), _re.IGNORECASE)
    if num_match:
        s = num_match.group(1)
    return s.strip()


def _squad_normalize(s: str) -> str:
    """SQuAD-style answer normalization: lowercase, strip articles, strip punctuation, fix whitespace."""
    import string
    s = s.lower()
    # Remove articles
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    # Remove punctuation (including quotes)
    exclude = set(string.punctuation)
    s = "".join(ch for ch in s if ch not in exclude)
    # Fix whitespace
    s = " ".join(s.split())
    return s


def _compute_f1(prediction: str, reference: str) -> float:
    """SQuAD-style token-level F1 score (with normalization, multiset counting)."""
    from collections import Counter as _Counter
    pred_norm = _squad_normalize(prediction)
    ref_norm = _squad_normalize(reference)
    pred_tokens = pred_norm.split()
    ref_tokens = ref_norm.split()
    if not pred_tokens or not ref_tokens:
        return 1.0 if pred_tokens == ref_tokens else 0.0
    # Use multiset intersection (accounts for repeated tokens like in DROP)
    common = _Counter(pred_tokens) & _Counter(ref_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(ref_tokens)
    return 2 * precision * recall / (precision + recall)


def _compute_exact_match(prediction: str, reference: str) -> bool:
    """SQuAD-style exact match after normalization."""
    return _squad_normalize(prediction) == _squad_normalize(reference)


def _run_quiet(cmd: Sequence[str]) -> str:
    try:
        completed = subprocess.run(list(cmd), capture_output=True, text=True, check=True)
    except Exception:
        return ""
    return (completed.stdout or "").strip()
