from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from omegaconf import DictConfig, OmegaConf

from dynaforge.benchmarks import (
    BenchmarkSetupError,
    grade_medqa_prediction,
    prepare_case_study_assets,
    prepare_medqa_assets,
    prepare_medqa_dataset,
    prepare_spatialbench_manifests,
    setup_spatialbench_workspace,
)
from dynaforge.config import build_blueprint_from_config, build_executor_from_config, resolve_hydra_config
from dynaforge.ir import (
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
from dynaforge.runtime import ExecutionCost, ExecutionReport, NodeExecutionResult
from dynaforge.ir.schema import FailureType
from dynaforge.runtime.llm import LLMClientError, OpenAICompatibleLLMClient
from dynaforge.secrets import load_local_secrets


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
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        return path

    def write_text(self, relative_path: str | Path, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


def run_stage0_validation(
    medqa_config: DictConfig | Mapping[str, Any],
    spatialbench_config: DictConfig | Mapping[str, Any],
    *,
    base_dir: str | Path = "runs",
    medqa_smoke_limit: int = 1,
) -> dict[str, Any]:
    load_local_secrets()
    recorder = RunRecorder("stage0_validation", base_dir=base_dir)
    medqa_resolved = resolve_hydra_config(medqa_config)
    spatial_resolved = resolve_hydra_config(spatialbench_config)

    recorder.write_text("config/medqa_config.yaml", _config_to_yaml(medqa_config))
    recorder.write_text("config/spatialbench_config.yaml", _config_to_yaml(spatialbench_config))
    metadata = collect_run_metadata(medqa_resolved)
    recorder.write_json("summaries/run_metadata.json", metadata)

    stage0_summary: dict[str, Any] = {
        "metadata": metadata,
        "capabilities": detect_runtime_capabilities(),
        "api_probe": probe_model_access(medqa_resolved.get("model", {})),
    }

    try:
        stage0_summary["medqa_assets"] = prepare_medqa_assets(medqa_resolved)
        stage0_summary["medqa_dataset"] = prepare_medqa_dataset(medqa_resolved)
        stage0_summary["medqa_smoke"] = run_medqa_experiment(
            medqa_config,
            subset="smoke",
            limit=medqa_smoke_limit,
            base_dir=recorder.root / "artifacts",
            run_name="stage0_medqa_smoke",
        )
    except Exception as exc:
        stage0_summary["medqa_error"] = str(exc)

    try:
        stage0_summary["spatialbench_workspace"] = setup_spatialbench_workspace(spatial_resolved)
        stage0_summary["spatialbench_manifest"] = prepare_spatialbench_manifests(spatial_resolved)
    except Exception as exc:
        stage0_summary["spatialbench_error"] = str(exc)

    stage0_summary["success"] = bool(
        stage0_summary["api_probe"].get("ok")
        and "medqa_error" not in stage0_summary
        and stage0_summary.get("medqa_smoke", {}).get("task_count", 0) > 0
    )
    recorder.write_json("summaries/stage0_summary.json", stage0_summary)
    recorder.write_text("summaries/stage0_summary.md", _render_stage0_markdown(stage0_summary))
    return {**stage0_summary, "run_dir": str(recorder.root)}


def run_medqa_experiment(
    config: DictConfig | Mapping[str, Any],
    *,
    subset: str = "smoke",
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
        run_label = f"medqa_{subset}_shard{shard_index:02d}of{num_shards:02d}"
    else:
        run_label = f"medqa_{subset}"
    recorder = RunRecorder(run_label, base_dir=base_dir, root_dir=resume_run_dir)
    recorder.write_text("config/resolved_config.yaml", _config_to_yaml(config))
    run_metadata = collect_run_metadata(resolved)
    if resume_run_dir is not None:
        run_metadata["resume_run_dir"] = str(Path(resume_run_dir).resolve())
        run_metadata["resumed"] = True

    medqa_assets = prepare_medqa_assets(resolved)
    medqa_manifest = prepare_medqa_dataset(resolved)
    benchmark_settings = dict(resolved.get("benchmark", {}))
    records = _read_jsonl(Path(medqa_manifest["records_path"]))
    indices = _resolve_medqa_indices(records, medqa_manifest, subset=subset, limit=limit, task_ids=task_ids)
    indices = _apply_shard(indices, num_shards=num_shards, shard_index=shard_index)
    recorder.write_json("eval/selected_indices.json", indices)
    sample_records = [
        {
            "order": order,
            "sample_index": index,
            "sample": records[index],
        }
        for order, index in enumerate(indices, start=1)
    ]
    sample_by_task_id = {
        str(item["sample"]["id"]): item
        for item in sample_records
    }

    base_blueprint = build_blueprint_from_config(resolved)
    executor = build_executor_from_config(resolved, artifact_root=recorder.artifacts_dir)
    preferred_nodes = benchmark_settings.get(
        "preferred_answer_nodes",
        ["reviser", "reviewer", "responder", "worker"],
    )
    task_results = _load_existing_medqa_task_results(
        recorder.root,
        sample_by_task_id=sample_by_task_id,
        preferred_nodes=preferred_nodes,
    )
    task_results_by_id = {
        str(result.get("task_id", "")): dict(result)
        for result in task_results
        if str(result.get("task_id", ""))
    }
    run_metadata["resume_completed_task_count"] = len(task_results_by_id)
    recorder.write_json("summaries/run_metadata.json", run_metadata)

    for order, index in enumerate(indices, start=1):
        sample = records[index]
        sample_id = str(sample["id"])
        if sample_id in task_results_by_id:
            continue
        task_blueprint = build_medqa_task_blueprint(base_blueprint, sample)
        try:
            _, report = _run_blueprint(resolved, task_blueprint, executor)
            task_result = _build_medqa_task_result(
                sample=sample,
                sample_index=index,
                order=order,
                report=report,
                recorder=recorder,
                preferred_nodes=preferred_nodes,
            )
        except Exception as exc:
            task_result = _build_medqa_exception_result(
                sample=sample,
                sample_index=index,
                order=order,
                recorder=recorder,
                error=exc,
            )
        task_results_by_id[sample_id] = task_result
        task_results = _sorted_task_results(task_results_by_id.values())
        recorder.write_json("eval/task_results.json", task_results)

    task_results = _sorted_task_results(task_results_by_id.values())
    summary = _summarize_medqa_task_results(
        task_results,
        subset=subset,
        requested_task_ids=list(task_ids or []),
        medqa_assets=medqa_assets,
        medqa_manifest=medqa_manifest,
        num_shards=num_shards,
        shard_index=shard_index,
    )
    summary["run_dir"] = str(recorder.root)

    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_medqa_analysis(summary, task_results))
    deep_analysis = build_medqa_deep_analysis(task_results)
    recorder.write_json("summaries/deep_analysis.json", deep_analysis)
    recorder.write_text("summaries/deep_analysis.md", render_medqa_deep_analysis(summary, deep_analysis))
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
    case_id = str(benchmark_settings.get("case_id", "")).strip()
    if not case_id:
        raise BenchmarkSetupError("Case-study runner requires benchmark.case_id")

    default_run_name = {
        "scanpy_pbmc3k_umap": "scanpy_pbmc3k_case",
        "squidpy_visium_hne_spatial": "squidpy_visium_case",
    }.get(case_id, "case_study")

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
        handlers = {"planner": _scanpy_planner_handler, "scanpy_tool": _scanpy_tool_handler}
        summarize = _summarize_scanpy_case_study
        render_analysis = render_scanpy_case_study_analysis
        render_narrative = render_scanpy_case_study_narrative
    elif case_id == "squidpy_visium_hne_spatial":
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
        handlers = {"planner": _squidpy_planner_handler, "squidpy_tool": _squidpy_tool_handler}
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


def run_scanpy_pbmc3k_case_study(
    config: DictConfig | Mapping[str, Any],
    *,
    base_dir: str | Path = "runs",
    run_name: Optional[str] = None,
) -> dict[str, Any]:
    return run_case_study_experiment(config, base_dir=base_dir, run_name=run_name)


def aggregate_medqa_runs(
    run_dirs: Sequence[str | Path],
    *,
    base_dir: str | Path = "runs",
    run_name: str = "medqa_aggregate",
) -> dict[str, Any]:
    if not run_dirs:
        raise BenchmarkSetupError("aggregate_medqa_runs requires at least one run directory")

    recorder = RunRecorder(run_name, base_dir=base_dir)
    all_results: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()

    for run_dir in [Path(path).resolve() for path in run_dirs]:
        task_results_path = run_dir / "eval" / "task_results.json"
        summary_path = run_dir / "summaries" / "summary.json"
        if not task_results_path.exists():
            raise BenchmarkSetupError(f"Missing MedQA task results: {task_results_path}")
        task_results = json.loads(task_results_path.read_text(encoding="utf-8"))
        for result in task_results:
            task_id = str(result.get("task_id", ""))
            if task_id in seen_task_ids:
                raise BenchmarkSetupError(f"Duplicate MedQA task_id in aggregate inputs: {task_id}")
            seen_task_ids.add(task_id)
            all_results.append(result)
        if summary_path.exists():
            source_summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))

    all_results.sort(key=lambda item: (int(item.get("sample_index", 0)), str(item.get("task_id", ""))))
    summary = _summarize_medqa_task_results(
        all_results,
        subset="aggregate",
        requested_task_ids=[],
        medqa_assets=source_summaries[0].get("medqa_assets", {}) if source_summaries else {},
        medqa_manifest=source_summaries[0].get("medqa_manifest", {}) if source_summaries else {},
        num_shards=len(run_dirs),
        shard_index=-1,
    )
    summary["source_run_dirs"] = [str(Path(path).resolve()) for path in run_dirs]
    summary["run_dir"] = str(recorder.root)

    recorder.write_json("eval/task_results.json", all_results)
    recorder.write_json("summaries/summary.json", summary)
    recorder.write_text("summaries/analysis.md", render_medqa_analysis(summary, all_results))
    deep_analysis = build_medqa_deep_analysis(all_results)
    recorder.write_json("summaries/deep_analysis.json", deep_analysis)
    recorder.write_text("summaries/deep_analysis.md", render_medqa_deep_analysis(summary, deep_analysis))
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
        from dynaforge.ir.schema import ModelSpec

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


def build_medqa_task_blueprint(base_blueprint: WorkflowBlueprint, sample: Mapping[str, Any]) -> WorkflowBlueprint:
    task_hints = dict(base_blueprint.task.hints)
    task_hints.update(
        {
            "question_id": sample.get("question_id", ""),
            "question": sample["question"],
            "options": sample["options"],
            "benchmark": "medqa",
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
    updated_meta.update({"sample_id": sample["id"], "question_id": sample.get("question_id", "")})
    return base_blueprint.model_copy(update={"task": updated_task, "meta": updated_meta}, deep=True)


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


def _resolve_medqa_indices(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    subset: str,
    limit: Optional[int],
    task_ids: Optional[Sequence[str]],
) -> list[int]:
    indices = _load_indices(manifest, subset=subset)
    if task_ids:
        requested = [task_id.strip() for task_id in task_ids if task_id and task_id.strip()]
        index_by_task = {str(records[index]["id"]): index for index in indices}
        index_by_question = {str(records[index].get("question_id", "")): index for index in indices}
        filtered: list[int] = []
        missing: list[str] = []
        for task_id in requested:
            index = index_by_task.get(task_id, index_by_question.get(task_id))
            if index is None:
                missing.append(task_id)
                continue
            if index not in filtered:
                filtered.append(index)
        if missing:
            raise BenchmarkSetupError(
                "Requested MedQA task IDs are not available in the selected subset: "
                + ", ".join(sorted(missing))
            )
        indices = filtered
    if limit is not None:
        indices = indices[:limit]
    return indices


def _apply_shard(indices: Sequence[int], *, num_shards: int, shard_index: int) -> list[int]:
    if num_shards <= 1:
        return list(indices)
    return [index for position, index in enumerate(indices) if position % num_shards == shard_index]


def _medqa_evaluation_failure_type(report: ExecutionReport, correct: bool) -> str:
    if report.failure_type.value != "none":
        return report.failure_type.value
    if not correct:
        return "incorrect_answer"
    return "none"


def _summarize_medqa_task_results(
    task_results: Sequence[Mapping[str, Any]],
    *,
    subset: str,
    requested_task_ids: Sequence[str],
    medqa_assets: Mapping[str, Any],
    medqa_manifest: Mapping[str, Any],
    num_shards: int,
    shard_index: int,
) -> dict[str, Any]:
    workflow_counter: Counter[str] = Counter()
    failure_counter: Counter[str] = Counter()
    gate_counter: Counter[str] = Counter()
    type_accuracy: dict[str, dict[str, int]] = {}
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

        metadata = result.get("metadata", {})
        metadata_type = str(metadata.get("type", "") or "unknown") if isinstance(metadata, Mapping) else "unknown"
        stats = type_accuracy.setdefault(metadata_type, {"correct": 0, "total": 0})
        stats["total"] += 1
        if result.get("correct"):
            stats["correct"] += 1

        cost = result.get("cost", {})
        if isinstance(cost, Mapping):
            total_usd += float(cost.get("usd", 0.0) or 0.0)
            total_input_tokens += int(cost.get("input_tokens", 0) or 0)
            total_output_tokens += int(cost.get("output_tokens", 0) or 0)
            total_latency += float(cost.get("wall_time_s", 0.0) or 0.0)

        total_activated_nodes += int(result.get("activated_node_count", 0) or 0)
        total_activated_subgraphs += int(result.get("activated_subgraph_count", 0) or 0)

    accuracy = _safe_ratio(sum(1 for result in task_results if result.get("correct")), len(task_results))
    return {
        "benchmark": "medqa",
        "subset": subset,
        "requested_task_ids": list(requested_task_ids),
        "num_shards": num_shards,
        "shard_index": shard_index,
        "task_count": len(task_results),
        "accuracy": accuracy,
        "pass_at_1": accuracy,
        "average_usd": _safe_ratio(total_usd, len(task_results)),
        "average_input_tokens": _safe_ratio(total_input_tokens, len(task_results)),
        "average_output_tokens": _safe_ratio(total_output_tokens, len(task_results)),
        "average_latency_s": _safe_ratio(total_latency, len(task_results)),
        "average_activated_nodes": _safe_ratio(total_activated_nodes, len(task_results)),
        "average_activated_subgraphs": _safe_ratio(total_activated_subgraphs, len(task_results)),
        "workflow_patterns": dict(workflow_counter.most_common()),
        "failure_breakdown": dict(failure_counter),
        "gate_activations": dict(gate_counter),
        "type_accuracy": {
            key: {
                "accuracy": _safe_ratio(value["correct"], value["total"]),
                "correct": value["correct"],
                "total": value["total"],
            }
            for key, value in sorted(type_accuracy.items())
        },
        "medqa_assets": dict(medqa_assets),
        "medqa_manifest": dict(medqa_manifest),
    }


def render_medqa_analysis(summary: Mapping[str, Any], task_results: Sequence[Mapping[str, Any]]) -> str:
    incorrect = [result for result in task_results if not result["correct"]]
    pattern_accuracy: dict[str, dict[str, int]] = {}
    for result in task_results:
        pattern = str(result["workflow_signature"] or "unknown")
        stats = pattern_accuracy.setdefault(pattern, {"correct": 0, "total": 0})
        stats["total"] += 1
        if result["correct"]:
            stats["correct"] += 1
    lines = [
        f"# MedQA {summary['subset']} analysis",
        "",
        f"- Task count: {summary['task_count']}",
        f"- Accuracy / Pass@1: {summary['accuracy']:.3f}",
        f"- Average USD: {summary['average_usd']:.6f}",
        f"- Average input tokens: {summary['average_input_tokens']:.2f}",
        f"- Average output tokens: {summary['average_output_tokens']:.2f}",
        f"- Average latency (reported): {summary['average_latency_s']:.2f}s",
        f"- Average activated nodes: {summary.get('average_activated_nodes', 0.0):.2f}",
        f"- Average activated subgraphs: {summary.get('average_activated_subgraphs', 0.0):.2f}",
        f"- Workflow patterns: {json.dumps(summary['workflow_patterns'], ensure_ascii=True)}",
        f"- Failure breakdown: {json.dumps(summary['failure_breakdown'], ensure_ascii=True)}",
        f"- Gate activations: {json.dumps(summary['gate_activations'], ensure_ascii=True)}",
        "",
        "## Type-Stratified Accuracy",
    ]
    type_accuracy = summary.get("type_accuracy", {})
    if not type_accuracy:
        lines.append("- None")
    else:
        for type_name, stats in type_accuracy.items():
            lines.append(
                f"- `{type_name}`: accuracy={stats['accuracy']:.3f} ({stats['correct']}/{stats['total']})"
            )

    lines.extend(
        [
            "",
            "## Workflow Pattern Accuracy",
        ]
    )
    for pattern, stats in sorted(pattern_accuracy.items()):
        lines.append(
            f"- `{pattern}`: accuracy={_safe_ratio(stats['correct'], stats['total']):.3f} ({stats['correct']}/{stats['total']})"
        )

    lines.extend(
        [
            "",
            "## Incorrect examples",
        ]
    )
    if not incorrect:
        lines.append("- None")
    for result in incorrect[:10]:
        lines.extend(
            [
                f"- `{result['task_id']}`: predicted `{result['prediction_label'] or result['prediction_text']}` vs gold `{result['gold_label']}`",
                f"  failure_type={result['failure_type']} runtime_failure_type={result['runtime_failure_type']} workflow={result['workflow_signature']} gates={result['activated_gates']}",
            ]
        )
    return "\n".join(lines) + "\n"


def build_medqa_deep_analysis(task_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    question_type_stats: dict[str, dict[str, float]] = {}
    review_failure_breakdown: Counter[str] = Counter()
    top_review_failures: list[dict[str, Any]] = []
    activated_node_distribution: Counter[int] = Counter()
    activated_subgraph_distribution: Counter[int] = Counter()
    role_frequency: Counter[str] = Counter()
    node_frequency: Counter[str] = Counter()
    workflow_by_question_type: dict[str, Counter[str]] = {}
    reviewed_task_ids: list[str] = []

    for result in task_results:
        question_type = _medqa_question_type(result)
        workflow_signature = str(result.get("workflow_signature", "") or "unknown")
        is_reviewed = "reviewer" in workflow_signature or "reviser" in workflow_signature
        correct = bool(result.get("correct"))
        failure_type = str(result.get("failure_type", "unknown") or "unknown")
        runtime_failure_type = str(result.get("runtime_failure_type", "unknown") or "unknown")
        usd = _cost_value(result, "usd")

        stats = question_type_stats.setdefault(
            question_type,
            {
                "count": 0,
                "correct": 0,
                "reviewed": 0,
                "review_correct": 0,
                "plain": 0,
                "plain_correct": 0,
                "total_usd": 0.0,
            },
        )
        stats["count"] += 1
        stats["correct"] += int(correct)
        stats["total_usd"] += usd
        if is_reviewed:
            reviewed_task_ids.append(str(result.get("task_id", "")))
            stats["reviewed"] += 1
            stats["review_correct"] += int(correct)
            if not correct:
                review_failure_breakdown[question_type] += 1
                top_review_failures.append(
                    {
                        "task_id": str(result.get("task_id", "")),
                        "question_type": question_type,
                        "prediction_label": result.get("prediction_label", ""),
                        "gold_label": result.get("gold_label", ""),
                        "workflow_signature": workflow_signature,
                        "confidence": result.get("confidence"),
                        "failure_type": failure_type,
                        "runtime_failure_type": runtime_failure_type,
                        "report_path": result.get("report_path", ""),
                    }
                )
        else:
            stats["plain"] += 1
            stats["plain_correct"] += int(correct)

        activated_node_distribution[int(result.get("activated_node_count", 0) or 0)] += 1
        activated_subgraph_distribution[int(result.get("activated_subgraph_count", 0) or 0)] += 1
        workflow_by_question_type.setdefault(question_type, Counter())[workflow_signature] += 1

        role_counts, node_counts = _trace_role_and_node_counts(result)
        role_frequency.update(role_counts)
        node_frequency.update(node_counts)

    question_type_breakdown = {}
    for question_type, stats in sorted(question_type_stats.items()):
        question_type_breakdown[question_type] = {
            "count": int(stats["count"]),
            "accuracy": _safe_ratio(stats["correct"], int(stats["count"])),
            "review_rate": _safe_ratio(stats["reviewed"], int(stats["count"])),
            "review_accuracy": _safe_ratio(stats["review_correct"], int(stats["reviewed"])),
            "plain_accuracy": _safe_ratio(stats["plain_correct"], int(stats["plain"])),
            "average_usd": _safe_ratio(stats["total_usd"], int(stats["count"])),
            "workflow_patterns": dict(workflow_by_question_type.get(question_type, Counter()).most_common()),
        }

    top_review_failures.sort(key=lambda item: (item["question_type"], item["task_id"]))

    return {
        "question_type_breakdown": question_type_breakdown,
        "review_task_count": len(reviewed_task_ids),
        "reviewed_task_ids": reviewed_task_ids,
        "review_failure_breakdown": dict(review_failure_breakdown.most_common()),
        "top_review_failures": top_review_failures[:25],
        "workflow_structure": {
            "activated_node_count_distribution": dict(sorted(activated_node_distribution.items())),
            "activated_subgraph_count_distribution": dict(sorted(activated_subgraph_distribution.items())),
            "role_frequency": dict(role_frequency.most_common()),
            "node_frequency": dict(node_frequency.most_common()),
        },
    }


def render_medqa_deep_analysis(summary: Mapping[str, Any], deep_analysis: Mapping[str, Any]) -> str:
    lines = [
        f"# MedQA {summary['subset']} deep analysis",
        "",
        f"- Task count: {summary['task_count']}",
        f"- Accuracy / Pass@1: {summary['accuracy']:.3f}",
        f"- Reviewed task count: {deep_analysis.get('review_task_count', 0)}",
        "",
        "## Question-Type Breakdown",
    ]

    question_type_breakdown = deep_analysis.get("question_type_breakdown", {})
    if not question_type_breakdown:
        lines.append("- None")
    else:
        for question_type, stats in question_type_breakdown.items():
            lines.append(
                f"- `{question_type}`: count={stats['count']} accuracy={stats['accuracy']:.3f} "
                f"review_rate={stats['review_rate']:.3f} review_accuracy={stats['review_accuracy']:.3f} "
                f"plain_accuracy={stats['plain_accuracy']:.3f} average_usd={stats['average_usd']:.6f}"
            )

    lines.extend(
        [
            "",
            "## Review Failure Breakdown",
        ]
    )
    review_failure_breakdown = deep_analysis.get("review_failure_breakdown", {})
    if not review_failure_breakdown:
        lines.append("- None")
    else:
        for question_type, count in review_failure_breakdown.items():
            lines.append(f"- `{question_type}`: {count}")

    lines.extend(
        [
            "",
            "## Workflow Structure",
            f"- Activated node count distribution: {json.dumps(deep_analysis.get('workflow_structure', {}).get('activated_node_count_distribution', {}), ensure_ascii=True)}",
            f"- Activated subgraph count distribution: {json.dumps(deep_analysis.get('workflow_structure', {}).get('activated_subgraph_count_distribution', {}), ensure_ascii=True)}",
            f"- Role frequency: {json.dumps(deep_analysis.get('workflow_structure', {}).get('role_frequency', {}), ensure_ascii=True)}",
            "",
            "## Top Review Failures",
        ]
    )
    top_review_failures = deep_analysis.get("top_review_failures", [])
    if not top_review_failures:
        lines.append("- None")
    else:
        for item in top_review_failures[:10]:
            lines.append(
                f"- `{item['task_id']}`: qtype={item['question_type']} pred={item['prediction_label']} "
                f"gold={item['gold_label']} workflow={item['workflow_signature']} confidence={item['confidence']}"
            )
    return "\n".join(lines) + "\n"


def _build_medqa_task_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    recorder: RunRecorder,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    grading = grade_medqa_prediction(answer_payload, sample)
    workflow_signature = _workflow_signature(report)
    evaluation_failure_type = _medqa_evaluation_failure_type(report, grading["correct"])
    question_type = _planner_question_type_from_report(report)
    report_path = recorder.write_json(f"traces/{sample['id']}.report.json", report.model_dump())
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question_id": sample.get("question_id", ""),
        "question": sample["question"],
        "options": sample["options"],
        "metadata": sample.get("metadata", {}),
        "prediction_node": answer_node,
        "question_type": question_type,
        **grading,
        "workflow_signature": workflow_signature,
        "activated_gates": report.activated_gates,
        "active_subgraphs": report.active_subgraphs,
        "activated_node_count": sum(1 for trace in report.traces if trace.status in {"success", "cached"}),
        "activated_subgraph_count": len(report.active_subgraphs),
        "runtime_failure_type": report.failure_type.value,
        "failure_type": evaluation_failure_type,
        "confidence": report.confidence,
        "summary": report.summary,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _build_medqa_exception_result(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    recorder: RunRecorder,
    error: Exception,
) -> dict[str, Any]:
    error_type = _classify_medqa_exception(error)
    recorder.write_json(
        f"logs/{sample['id']}.runner_error.json",
        {
            "task_id": sample["id"],
            "question_id": sample.get("question_id", ""),
            "sample_index": sample_index,
            "error_type": error_type,
            "error_class": type(error).__name__,
            "error": str(error),
        },
    )
    grading = grade_medqa_prediction({}, sample)
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question_id": sample.get("question_id", ""),
        "question": sample["question"],
        "options": sample["options"],
        "metadata": sample.get("metadata", {}),
        "prediction_node": "",
        "question_type": "unknown",
        **grading,
        "workflow_signature": "",
        "activated_gates": [],
        "active_subgraphs": [],
        "activated_node_count": 0,
        "activated_subgraph_count": 0,
        "runtime_failure_type": error_type,
        "failure_type": error_type,
        "confidence": 0.0,
        "summary": str(error),
        "report_path": "",
        "cost": {},
        "error_class": type(error).__name__,
        "error": str(error),
    }


def _load_existing_medqa_task_results(
    run_root: Path,
    *,
    sample_by_task_id: Mapping[str, Mapping[str, Any]],
    preferred_nodes: Sequence[str],
) -> list[dict[str, Any]]:
    task_results_path = run_root / "eval" / "task_results.json"
    if task_results_path.exists():
        payload = json.loads(task_results_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return _sorted_task_results(payload)

    reconstructed: list[dict[str, Any]] = []
    for sample_id, sample_record in sample_by_task_id.items():
        sample = sample_record["sample"]
        report_path = run_root / "traces" / f"{sample_id}.report.json"
        if not report_path.exists():
            continue
        report = ExecutionReport.model_validate(json.loads(report_path.read_text(encoding="utf-8")))
        reconstructed.append(
            _build_medqa_task_result_from_existing_report(
                sample=sample,
                sample_index=int(sample_record["sample_index"]),
                order=int(sample_record["order"]),
                report=report,
                report_path=report_path,
                preferred_nodes=preferred_nodes,
            )
        )
    return _sorted_task_results(reconstructed)


def _build_medqa_task_result_from_existing_report(
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    order: int,
    report: ExecutionReport,
    report_path: Path,
    preferred_nodes: Sequence[str],
) -> dict[str, Any]:
    answer_payload, answer_node = extract_answer_payload(report, preferred_nodes=preferred_nodes)
    grading = grade_medqa_prediction(answer_payload, sample)
    workflow_signature = _workflow_signature(report)
    evaluation_failure_type = _medqa_evaluation_failure_type(report, grading["correct"])
    question_type = _planner_question_type_from_report(report)
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question_id": sample.get("question_id", ""),
        "question": sample["question"],
        "options": sample["options"],
        "metadata": sample.get("metadata", {}),
        "prediction_node": answer_node,
        "question_type": question_type,
        **grading,
        "workflow_signature": workflow_signature,
        "activated_gates": report.activated_gates,
        "active_subgraphs": report.active_subgraphs,
        "activated_node_count": sum(1 for trace in report.traces if trace.status in {"success", "cached"}),
        "activated_subgraph_count": len(report.active_subgraphs),
        "runtime_failure_type": report.failure_type.value,
        "failure_type": evaluation_failure_type,
        "confidence": report.confidence,
        "summary": report.summary,
        "report_path": str(report_path),
        "cost": report.cost.model_dump(),
    }


def _classify_medqa_exception(error: Exception) -> str:
    if isinstance(error, LLMClientError):
        return "tool_runtime_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "unknown"


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
            "dynaforge.case_studies.scanpy_pbmc3k_job",
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
            "dynaforge.case_studies.squidpy_visium_job",
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
            "Visium H&E Reference Spatial Plot",
        ],
        timeout_s=int(sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        raise BenchmarkSetupError(
            f"Failed to generate Squidpy Visium reference assets: {completed.stderr or completed.stdout}"
        )
    return {
        "reference_figure_path": str(reference_figure_path),
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "generated": True,
        "stdout": completed.stdout,
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
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["selected_repo", "analysis_config", "summary"],
                "properties": {
                    "selected_repo": {"type": "string"},
                    "analysis_config": {"type": "object"},
                    "summary": {"type": "string"},
                },
            }
        ),
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
        io=IOContract(
            output_schema={
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
        ),
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
                stdio_cmd=["python", "-m", "dynaforge.case_studies.scanpy_pbmc3k_server"],
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
    methods_excerpt = Path(str(case_assets["methods_excerpt_path"])).read_text(encoding="utf-8").strip()
    target_description = Path(str(case_assets["target_figure_description_path"])).read_text(encoding="utf-8").strip()
    candidate_repos = json.loads(Path(str(case_assets["candidate_repos_path"])).read_text(encoding="utf-8"))

    output_dir = recorder.artifacts_dir / "generated"
    figure_path = output_dir / "generated_spatial.png"
    summary_path = output_dir / "generated_summary.json"
    raw_data_path = recorder.artifacts_dir / "visium_hne_adata_crop.h5ad"
    task_description = "\n\n".join(
        [
            "Reproduce the target Visium H&E spatial plot as faithfully as possible using one of the candidate repos.",
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
        "figure_title": "Visium H&E Spatial Plot",
    }

    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="SpatialBioPlanner",
        description="Select the best candidate repo and a concrete Squidpy visualization configuration for a Visium spatial plot.",
        model=model_spec,
        system_prompt=(
            "You are a spatial-omics workflow planner. Choose the most appropriate candidate repo for execution, "
            "stay close to the methods excerpt, and output JSON with keys selected_repo, analysis_config, and summary. "
            "selected_repo must be one of the provided candidate repo names."
        ),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["selected_repo", "analysis_config", "summary"],
                "properties": {
                    "selected_repo": {"type": "string"},
                    "analysis_config": {"type": "object"},
                    "summary": {"type": "string"},
                },
            }
        ),
        max_steps=1,
    )
    tool_node = NodeSpec(
        node_id="squidpy_tool",
        kind=NodeKind.tool,
        role="SquidpySandboxAgent",
        description="Run the Visium spatial-plot pipeline inside an isolated sandbox via MCP.",
        sandbox=sandbox,
        tools=[ToolRef(server="squidpy_visium", tool="generate_spatial_plot")],
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
        description="Assess methodological faithfulness and biological plausibility of the reproduced Visium spatial figure.",
        model=review_model,
        system_prompt=(
            "You are reviewing a spatial-transcriptomics figure reproduction. "
            "Use the methods excerpt, target description, and generated summary to score methodology_faithfulness, "
            "biological_plausibility, and artifact_completeness on a 1-10 scale. "
            "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, "
            "overall_verdict, concerns, and summary."
        ),
        io=IOContract(
            output_schema={
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
        ),
    )
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_squidpy_visium_case.py"
    return WorkflowBlueprint(
        task=TaskSpec(
            task_id="squidpy_visium_case",
            title=str(benchmark_settings.get("title", "Squidpy Visium H&E spatial plot reproduction")),
            description=task_description,
            hints=task_hints,
        ),
        budget=budget,
        mcp_servers=[
            MCPServerRef(
                name="squidpy_visium",
                transport="stdio",
                stdio_cmd=["python", "-m", "dynaforge.case_studies.squidpy_visium_server"],
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
        eval=EvalSpec(
            hard_checks=[
                HardCheck(
                    check_id="squidpy_visium_hard_check",
                    description="Validate generated Visium spatial figure and summary against the reference summary.",
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
        for key in ("figure_path", "summary_path", "raw_data_path"):
            value = summary.get(key)
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


def _scanpy_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _sandbox_tool_handler(node, inputs, context)


def _squidpy_tool_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _sandbox_tool_handler(node, inputs, context)


def _scanpy_planner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _deterministic_case_planner_handler(
        inputs,
        preferred_repo="scanpy",
        summary="Deterministic case-study planner selected the executable Scanpy repo and preserved the configured PBMC3k pipeline.",
    )


def _squidpy_planner_handler(node: NodeSpec, inputs: Mapping[str, Any], context: Any) -> NodeExecutionResult:
    return _deterministic_case_planner_handler(
        inputs,
        preferred_repo="squidpy",
        summary="Deterministic case-study planner selected the executable Squidpy repo and preserved the configured Visium plotting pipeline.",
    )


def _summarize_scanpy_case_study(
    report: ExecutionReport,
    *,
    case_assets: Mapping[str, Any],
    reference_assets: Mapping[str, Any],
) -> dict[str, Any]:
    planner_output = report.node_results.get("planner", NodeExecutionResult()).outputs
    tool_payload = report.node_results.get("scanpy_tool", NodeExecutionResult()).outputs.get("result", {})
    reviewer_output = report.node_results.get("bio_reviewer", NodeExecutionResult()).outputs
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
        "tool_output": tool_payload,
        "review_output": reviewer_output,
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
        "",
        "## Planner",
        f"- Output: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
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
            f"Sandboxed tool result: repo={selected_repo}, clusters={cluster_count}, figure={summary.get('generated_figure_path', '')}",
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
    planner_output = report.node_results.get("planner", NodeExecutionResult()).outputs
    tool_payload = report.node_results.get("squidpy_tool", NodeExecutionResult()).outputs.get("result", {})
    reviewer_output = report.node_results.get("bio_reviewer", NodeExecutionResult()).outputs
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
        "tool_output": tool_payload,
        "review_output": reviewer_output,
        "generated_figure_path": tool_summary.get("figure_path", ""),
        "generated_summary_path": tool_summary.get("summary_path", ""),
        "generated_raw_data_path": tool_summary.get("raw_data_path", ""),
        "reference_figure_path": reference_assets["reference_figure_path"],
        "reference_summary_path": reference_assets["reference_summary_path"],
    }


def render_squidpy_case_study_analysis(summary: Mapping[str, Any]) -> str:
    review_output = summary.get("review_output", {})
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    lines = [
        "# Squidpy Visium case-study analysis",
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
        "",
        "## Planner",
        f"- Output: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
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
        "",
        "## Reviewer",
        f"- Output: {json.dumps(review_output, ensure_ascii=True)}",
    ]
    if summary.get("contract_violations"):
        lines.extend(["", "## Contract Violations"])
        for item in summary["contract_violations"]:
            lines.append(f"- {item['node_id']}: {item['message']}")
    return "\n".join(lines) + "\n"


def render_squidpy_case_study_narrative(summary: Mapping[str, Any]) -> str:
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, Mapping) else {}
    review_output = summary.get("review_output", {})
    selected_repo = tool_output.get("selected_repo", "") if isinstance(tool_output, Mapping) else ""
    cluster_count = tool_summary.get("cluster_count", "") if isinstance(tool_summary, Mapping) else ""
    return "\n".join(
        [
            "# Squidpy Visium Experiment 3 Narrative",
            "",
            "This run used a sandboxed Squidpy environment cloned from an external candidate repository, "
            "wrapped as an MCP tool, to reproduce a Visium H&E spatial plot from a methods excerpt and target figure description.",
            "",
            f"Planner choice: {json.dumps(summary.get('planner_output', {}), ensure_ascii=True)}",
            f"Sandboxed tool result: repo={selected_repo}, clusters={cluster_count}, figure={summary.get('generated_figure_path', '')}",
            f"Reviewer assessment: {json.dumps(review_output, ensure_ascii=True)}",
            f"Hard-check status: {json.dumps(summary.get('hard_checks', []), ensure_ascii=True)}",
            "",
            f"Final status: success={summary.get('success', False)} failure_type={summary.get('failure_type', 'none')}",
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
    if "medqa_smoke" in summary:
        lines.append(f"- MedQA smoke accuracy: {summary['medqa_smoke'].get('accuracy', 0.0):.3f}")
    if "medqa_error" in summary:
        lines.append(f"- MedQA error: {summary['medqa_error']}")
    if "spatialbench_error" in summary:
        lines.append(f"- SpatialBench error: {summary['spatialbench_error']}")
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
        if trace.node_id != "planner":
            continue
        outputs = trace.outputs or {}
        question_type = outputs.get("question_type")
        if isinstance(question_type, str) and question_type.strip():
            return question_type.strip()
    return "unknown"


def _medqa_question_type(result: Mapping[str, Any]) -> str:
    direct = result.get("question_type")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    report_path = result.get("report_path")
    if isinstance(report_path, str) and report_path:
        path = Path(report_path)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for trace in payload.get("traces", []):
                    if trace.get("node_id") != "planner":
                        continue
                    outputs = trace.get("outputs", {})
                    if isinstance(outputs, Mapping):
                        question_type = outputs.get("question_type")
                        if isinstance(question_type, str) and question_type.strip():
                            return question_type.strip()
            except Exception:
                return "unknown"
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


def _safe_ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _slugify(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part) or "run"


def _run_quiet(cmd: Sequence[str]) -> str:
    try:
        completed = subprocess.run(list(cmd), capture_output=True, text=True, check=True)
    except Exception:
        return ""
    return (completed.stdout or "").strip()
