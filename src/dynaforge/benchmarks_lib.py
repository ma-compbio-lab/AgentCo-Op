"""Thin re-export shim — the actual implementation lives in dynaforge.benchmarks.

This module exists so that experiment_runner.py and other internal callers can
import from ``dynaforge.benchmarks_lib`` while the public API remains available
under ``dynaforge.benchmarks`` (a package that also exposes the new runner
classes).
"""
from __future__ import annotations

from dynaforge.benchmarks import (
    BenchmarkSetupError,
    _ensure_huggingface_cache_is_writable,
    _math_equal,
    _path_is_writable,
    _import_datasets_module,
    _prepare_aflow_public_benchmark_archive,
    _prepare_math_upstream_assets,
    _prepare_humaneval_upstream_assets,
    _read_aflow_jsonl_records,
    extract_humaneval_completion,
    grade_humaneval_prediction,
    grade_math_prediction,
    normalize_humaneval_record,
    normalize_math_record,
    parse_math_answer,
    prepare_case_study_assets,
    prepare_generic_case_study_assets,
    prepare_humaneval_dataset,
    prepare_math_dataset,
    probe_humaneval_call,
    run_humaneval_public_tests,
)

__all__ = [
    "BenchmarkSetupError",
    "_ensure_huggingface_cache_is_writable",
    "_math_equal",
    "_path_is_writable",
    "_import_datasets_module",
    "_prepare_aflow_public_benchmark_archive",
    "_prepare_math_upstream_assets",
    "_prepare_humaneval_upstream_assets",
    "_read_aflow_jsonl_records",
    "extract_humaneval_completion",
    "grade_humaneval_prediction",
    "grade_math_prediction",
    "normalize_humaneval_record",
    "normalize_math_record",
    "parse_math_answer",
    "prepare_case_study_assets",
    "prepare_generic_case_study_assets",
    "prepare_humaneval_dataset",
    "prepare_math_dataset",
    "probe_humaneval_call",
    "run_humaneval_public_tests",
]
