from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from agentcoop.benchmarks.runner_base import BenchmarkRunner
from agentcoop.ir.schema import WorkflowBlueprint
from agentcoop.runtime.reports import ExecutionReport


class MBPPRunner(BenchmarkRunner):
    benchmark_name = "mbpp"
    default_preferred_nodes = ("rewriter", "reviser", "coder")

    def prepare_dataset(self, resolved: Mapping[str, Any]) -> Dict[str, Any]:
        from agentcoop.benchmarks_lib import prepare_mbpp_dataset
        return prepare_mbpp_dataset(resolved)

    def build_task_blueprint(
        self, base_blueprint: WorkflowBlueprint, sample: Dict[str, Any]
    ) -> WorkflowBlueprint:
        from agentcoop.experiment_runner import build_mbpp_task_blueprint
        return build_mbpp_task_blueprint(base_blueprint, sample)

    def build_task_handlers(
        self,
        resolved: Mapping[str, Any],
        blueprint: WorkflowBlueprint,
        sample: Dict[str, Any],
    ) -> Dict[str, Any]:
        from agentcoop.experiment_runner import _build_mbpp_task_handlers
        return _build_mbpp_task_handlers(resolved, blueprint, sample)

    def build_task_result(
        self,
        *,
        sample: Dict[str, Any],
        sample_index: int,
        order: int,
        report: ExecutionReport,
        report_path: Path,
        preferred_nodes: Tuple[str, ...],
    ) -> Dict[str, Any]:
        from agentcoop.experiment_runner import _build_mbpp_task_result
        return _build_mbpp_task_result(
            sample=sample,
            sample_index=sample_index,
            order=order,
            report=report,
            report_path=report_path,
            preferred_nodes=preferred_nodes,
        )

    def summarize(
        self,
        task_results: List[Dict[str, Any]],
        *,
        subset: str,
        manifest: Dict[str, Any],
        requested_task_ids: List[str],
        num_shards: int,
        shard_index: int,
    ) -> Dict[str, Any]:
        from agentcoop.experiment_runner import _summarize_generic_task_results
        return _summarize_generic_task_results(
            benchmark="mbpp",
            metric_name="pass_at_1",
            task_results=task_results,
            subset=subset,
            manifest=manifest,
            requested_task_ids=requested_task_ids,
            num_shards=num_shards,
            shard_index=shard_index,
        )
