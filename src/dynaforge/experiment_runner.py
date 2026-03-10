from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
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
    prepare_medqa_assets,
    prepare_medqa_dataset,
    prepare_spatialbench_manifests,
    setup_spatialbench_workspace,
)
from dynaforge.config import build_blueprint_from_config, build_executor_from_config, resolve_hydra_config
from dynaforge.ir import WorkflowBlueprint
from dynaforge.runtime import ExecutionReport
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
    return summary


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
    return {
        "order": order,
        "sample_index": sample_index,
        "task_id": sample["id"],
        "question_id": sample.get("question_id", ""),
        "question": sample["question"],
        "options": sample["options"],
        "metadata": sample.get("metadata", {}),
        "prediction_node": answer_node,
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
) -> tuple[WorkflowBlueprint, ExecutionReport]:
    executor_cfg = _executor_settings(config)
    if executor_cfg.get("repair_enabled", False):
        return executor.execute_with_repair(
            blueprint,
            max_iterations=int(executor_cfg.get("max_repair_iterations", 3)),
        )
    return blueprint, executor.execute_blueprint(blueprint)


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
