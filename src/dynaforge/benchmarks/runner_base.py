from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from omegaconf import DictConfig

from dynaforge.benchmarks_lib import BenchmarkSetupError
from dynaforge.config import build_blueprint_from_config, build_executor_from_config, resolve_hydra_config
from dynaforge.experiment_runner import (
    RunRecorder,
    _apply_shard,
    _config_to_yaml,
    _load_existing_generic_task_results,
    _preferred_answer_nodes,
    _read_jsonl,
    _resolve_generic_indices,
    _run_blueprint,
    _sorted_task_results,
    collect_run_metadata,
)
from dynaforge.ir.schema import WorkflowBlueprint
from dynaforge.runtime.reports import ExecutionReport
from dynaforge.secrets import load_local_secrets


@dataclass
class RunResult:
    summary: Dict[str, Any]
    run_dir: Path


class BenchmarkRunner(abc.ABC):
    """Abstract base for MATH and HumanEval benchmark runners."""

    @property
    @abc.abstractmethod
    def benchmark_name(self) -> str: ...

    @abc.abstractmethod
    def prepare_dataset(self, resolved: Mapping[str, Any]) -> Dict[str, Any]: ...

    @property
    @abc.abstractmethod
    def default_preferred_nodes(self) -> tuple[str, ...]: ...

    @abc.abstractmethod
    def build_task_blueprint(
        self, base_blueprint: WorkflowBlueprint, sample: Dict[str, Any]
    ) -> WorkflowBlueprint: ...

    def build_task_handlers(
        self,
        resolved: Mapping[str, Any],
        blueprint: WorkflowBlueprint,
        sample: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {}

    @abc.abstractmethod
    def build_task_result(
        self,
        *,
        sample: Dict[str, Any],
        sample_index: int,
        order: int,
        report: ExecutionReport,
        report_path: Path,
        preferred_nodes: tuple[str, ...],
    ) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def summarize(
        self,
        task_results: List[Dict[str, Any]],
        *,
        subset: str,
        manifest: Dict[str, Any],
        requested_task_ids: List[str],
        num_shards: int,
        shard_index: int,
    ) -> Dict[str, Any]: ...

    def run(
        self,
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
    ) -> Dict[str, Any]:
        load_local_secrets()
        resolved = resolve_hydra_config(config)
        if num_shards < 1:
            raise BenchmarkSetupError("num_shards must be >= 1")
        if shard_index < 0 or shard_index >= num_shards:
            raise BenchmarkSetupError("shard_index must satisfy 0 <= shard_index < num_shards")

        if run_name is not None:
            run_label = run_name
        elif num_shards > 1:
            run_label = f"{self.benchmark_name}_{subset}_shard{shard_index:02d}of{num_shards:02d}"
        else:
            run_label = f"{self.benchmark_name}_{subset}"

        recorder = RunRecorder(run_label, base_dir=base_dir, root_dir=resume_run_dir)
        recorder.write_text("config/resolved_config.yaml", _config_to_yaml(config))
        run_metadata = collect_run_metadata(resolved)
        if resume_run_dir is not None:
            run_metadata["resume_run_dir"] = str(Path(resume_run_dir).resolve())
            run_metadata["resumed"] = True

        manifest = self.prepare_dataset(resolved)
        records = _read_jsonl(Path(manifest["records_path"]))
        indices = _resolve_generic_indices(records, manifest, subset=subset, limit=limit, task_ids=task_ids)
        indices = _apply_shard(indices, num_shards=num_shards, shard_index=shard_index)
        recorder.write_json("eval/selected_indices.json", indices)

        base_blueprint = build_blueprint_from_config(resolved)
        executor = build_executor_from_config(resolved, artifact_root=recorder.artifacts_dir)
        preferred_nodes = _preferred_answer_nodes(resolved, default=self.default_preferred_nodes)

        task_results_by_id = self._load_existing(recorder.root, records, indices, preferred_nodes)
        recorder.write_json("summaries/run_metadata.json", run_metadata)

        for order, index in enumerate(indices, start=1):
            sample = records[index]
            sample_id = str(sample["id"])
            if sample_id in task_results_by_id:
                continue
            task_blueprint = self.build_task_blueprint(base_blueprint, sample)
            handlers = self.build_task_handlers(resolved, task_blueprint, sample)
            _, report = _run_blueprint(resolved, task_blueprint, executor, handlers=handlers)
            report_path = recorder.write_json(f"traces/{sample_id}.report.json", report.model_dump())
            task_result = self.build_task_result(
                sample=sample,
                sample_index=index,
                order=order,
                report=report,
                report_path=report_path,
                preferred_nodes=preferred_nodes,
            )
            task_results_by_id[sample_id] = task_result
            recorder.write_json("eval/task_results.json", _sorted_task_results(task_results_by_id.values()))

        task_results = _sorted_task_results(task_results_by_id.values())
        summary = self.summarize(
            task_results,
            subset=subset,
            manifest=manifest,
            requested_task_ids=list(task_ids or []),
            num_shards=num_shards,
            shard_index=shard_index,
        )
        summary["run_dir"] = str(recorder.root)
        recorder.write_json("summaries/summary.json", summary)
        return summary

    def _load_existing(
        self,
        run_root: Path,
        records: List[Dict[str, Any]],
        indices: List[int],
        preferred_nodes: tuple[str, ...],
    ) -> Dict[str, Dict[str, Any]]:
        results = _load_existing_generic_task_results(
            run_root=run_root,
            records=records,
            selected_indices=indices,
            preferred_nodes=preferred_nodes,
            result_builder=lambda *, sample, sample_index, order, report, report_path: self.build_task_result(
                sample=sample,
                sample_index=sample_index,
                order=order,
                report=report,
                report_path=report_path,
                preferred_nodes=preferred_nodes,
            ),
        )
        return {str(r.get("task_id", "")): dict(r) for r in results}
