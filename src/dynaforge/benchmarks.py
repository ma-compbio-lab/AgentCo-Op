from __future__ import annotations

import json
from contextlib import contextmanager
import os
import random
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from dynaforge.case_studies.legacy_registry import legacy_case_family
from dynaforge.config import get_benchmark_settings

try:  # pragma: no cover - platform-dependent
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


class BenchmarkSetupError(RuntimeError):
    """Raised when a benchmark asset or environment setup step fails."""


_OPTION_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_AFLOW_MATH_TYPES = (
    "Counting & Probability",
    "Number Theory",
    "Prealgebra",
    "Precalculus",
)
_AFLOW_DATASET_URL = "https://drive.google.com/uc?export=download&id=1DNoegtZiUhWtvkd2xoIuElmIi4ah7k8e"


def prepare_medqa_assets(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "medqa":
        raise BenchmarkSetupError("Benchmark config kind must be 'medqa'")

    dataset_id = str(settings.get("dataset_id", "bigbio/med_qa"))
    trust_remote_code = bool(settings.get("trust_remote_code", True))
    preview_path = Path(str(settings.get("preview_path", "benchmarks/medqa/medqa_preview.json")))
    info_path = Path(str(settings.get("info_path", "benchmarks/medqa/medqa_dataset_info.json")))
    sample_count = int(settings.get("sample_count", 1))
    requested_config = settings.get("config_name")
    requested_split = settings.get("split")

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.parent.mkdir(parents=True, exist_ok=True)

    cached_info = _load_cached_json(info_path)
    if cached_info and preview_path.exists() and str(cached_info.get("dataset_id", "")) == dataset_id:
        return cached_info

    datasets_module = _import_datasets_module()

    config_names = list(
        datasets_module.get_dataset_config_names(
            dataset_id,
            trust_remote_code=trust_remote_code,
        )
    )
    if not config_names:
        raise BenchmarkSetupError(f"No dataset configs found for {dataset_id}")

    chosen_config = str(requested_config) if requested_config in config_names else _choose_medqa_config(config_names)
    split_candidates = _ordered_unique(
        [
            str(requested_split) if requested_split else None,
            "train",
            "validation",
            "test",
        ]
    )

    chosen_split: str | None = None
    records: list[Any] | None = None
    last_error: Exception | None = None
    for split_name in split_candidates:
        if split_name is None:
            continue
        try:
            dataset = datasets_module.load_dataset(
                dataset_id,
                chosen_config,
                split=f"{split_name}[:{sample_count}]",
                trust_remote_code=trust_remote_code,
            )
            records = list(dataset)
            chosen_split = split_name
            break
        except Exception as exc:  # pragma: no cover - exercised by real runs
            last_error = exc

    if records is None or chosen_split is None:
        raise BenchmarkSetupError(f"Unable to load any requested split for {dataset_id}: {last_error}")

    _write_json(preview_path, records)
    metadata = {
        "dataset_id": dataset_id,
        "datasets_version": str(getattr(datasets_module, "__version__", "unknown")),
        "trust_remote_code": trust_remote_code,
        "configs": config_names,
        "chosen_config": chosen_config,
        "chosen_split": chosen_split,
        "sample_count_loaded": len(records),
        "preview_path": str(preview_path),
    }
    _write_json(info_path, metadata)
    return metadata


def prepare_math_dataset(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "math":
        raise BenchmarkSetupError("Benchmark config kind must be 'math'")

    data_source = str(settings.get("data_source", "huggingface")).strip().lower()
    dataset_id = str(settings.get("dataset_id", "EleutherAI/hendrycks_math"))
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/math/processed"))).resolve()
    records_path = processed_dir / "records.jsonl"
    smoke_indices_path = processed_dir / "smoke_indices.json"
    validation_indices_path = processed_dir / "validation_indices.json"
    test_indices_path = processed_dir / "test_indices.json"
    metadata_path = processed_dir / "dataset_manifest.json"
    smoke_count = _coerce_desired_count(settings.get("smoke_count", 20))
    split_seed = int(settings.get("split_seed", 42))
    validation_fraction = float(settings.get("validation_fraction", 0.2))
    level_filter = str(settings.get("level_filter", "Level 5")).strip()
    source_split = str(settings.get("source_split", "test"))
    selected_types = tuple(str(item).strip() for item in settings.get("selected_types", _AFLOW_MATH_TYPES))

    processed_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(processed_dir / ".prepare.lock"):
        cached_manifest = _load_generic_cached_manifest(
            metadata_path,
            required_fields={
                "dataset_id": dataset_id,
                "data_source": data_source,
                "source_split": source_split,
                "split_seed": split_seed,
                "validation_fraction": validation_fraction,
                "level_filter": level_filter,
                "selected_types": list(selected_types),
            },
        )
        if cached_manifest is not None:
            upstream_manifest = _prepare_optional_upstream_manifest(
                _prepare_math_upstream_assets,
                settings,
                dataset_id=dataset_id,
                split_seed=split_seed,
            )
            cached_manifest = dict(cached_manifest)
            if upstream_manifest is not None:
                cached_manifest["upstream_full_manifest_path"] = str(Path(upstream_manifest["manifest_path"]).resolve())
                cached_manifest["upstream_train_count"] = int(upstream_manifest.get("train_count", 0))
                cached_manifest["upstream_test_count"] = int(upstream_manifest.get("test_count", 0))
                cached_manifest["upstream_train_sample_count"] = int(upstream_manifest.get("train_sample_count", 0))
            _write_json(metadata_path, cached_manifest)
            return cached_manifest

        if data_source == "aflow_package":
            archive_path = _prepare_aflow_public_benchmark_archive(settings)
            validation_source_records = _read_aflow_jsonl_records(archive_path, "math_validate.jsonl")
            test_source_records = _read_aflow_jsonl_records(archive_path, "math_test.jsonl")
            validation_records = [
                {
                    **normalize_math_record(item, config_name="aflow_validate", record_index=index),
                    "source_split": "validation",
                }
                for index, item in enumerate(validation_source_records)
            ]
            test_records = [
                {
                    **normalize_math_record(item, config_name="aflow_test", record_index=index),
                    "source_split": "test",
                }
                for index, item in enumerate(test_source_records)
            ]
            records = [*validation_records, *test_records]
            validation_indices = list(range(len(validation_records)))
            test_indices = list(range(len(validation_records), len(records)))
            config_names = ["aflow_validate", "aflow_test"]
        else:
            datasets_module = _import_datasets_module()
            config_names = list(settings.get("config_names") or datasets_module.get_dataset_config_names(dataset_id))
            if not config_names:
                raise BenchmarkSetupError(f"No dataset configs found for {dataset_id}")

            records = []
            for config_name in config_names:
                dataset = datasets_module.load_dataset(dataset_id, config_name, split=source_split)
                for record_index, item in enumerate(dataset):
                    normalized = normalize_math_record(item, config_name=config_name, record_index=record_index)
                    if level_filter and normalized["level"] != level_filter:
                        continue
                    if selected_types and normalized["type"] not in selected_types:
                        continue
                    records.append(normalized)
            records.sort(key=lambda item: str(item["id"]))
            validation_indices, test_indices = _split_validation_test_indices(
                len(records),
                validation_fraction=validation_fraction,
                seed=split_seed,
            )

        _write_jsonl(records_path, records)
        smoke_source = validation_indices if validation_indices else test_indices
        smoke_goal = len(smoke_source) if smoke_count is None else min(smoke_count, len(smoke_source))
        smoke_indices = smoke_source[:smoke_goal]
        _write_json(smoke_indices_path, smoke_indices)
        _write_json(validation_indices_path, validation_indices)
        _write_json(test_indices_path, test_indices)

        manifest = {
            "dataset_id": dataset_id,
            "data_source": data_source,
            "config_names": config_names,
            "source_split": source_split,
            "record_count": len(records),
            "smoke_count": len(smoke_indices),
            "validation_count": len(validation_indices),
            "test_count": len(test_indices),
            "split_seed": split_seed,
            "validation_fraction": validation_fraction,
            "level_filter": level_filter,
            "selected_types": list(selected_types),
            "records_path": str(records_path),
            "smoke_indices_path": str(smoke_indices_path),
            "validation_indices_path": str(validation_indices_path),
            "test_indices_path": str(test_indices_path),
            "metric": "solve_rate",
            "paper_reference_subset": "AFlow MATH Level-5 public subset",
            "paper_reference_expected_count": 617,
            "paper_reference_observed_count": len(records),
            "paper_reference_note": (
                "AFlow reports 617 level-5 MATH problems across four subject areas in the paper text. "
                "The official packaged public split and the current Hugging Face mirror both yield the observed count recorded here."
            ),
        }
        if data_source == "aflow_package":
            manifest["aflow_archive_path"] = str(archive_path)
        upstream_manifest = _prepare_optional_upstream_manifest(
            _prepare_math_upstream_assets,
            settings,
            dataset_id=dataset_id,
            split_seed=split_seed,
        )
        if upstream_manifest is not None:
            manifest["upstream_full_manifest_path"] = str(Path(upstream_manifest["manifest_path"]).resolve())
            manifest["upstream_train_count"] = int(upstream_manifest.get("train_count", 0))
            manifest["upstream_test_count"] = int(upstream_manifest.get("test_count", 0))
            manifest["upstream_train_sample_count"] = int(upstream_manifest.get("train_sample_count", 0))
        _write_json(metadata_path, manifest)
        return manifest


def normalize_math_record(
    record: Mapping[str, Any],
    *,
    config_name: str,
    record_index: int,
) -> dict[str, Any]:
    problem = str(record.get("problem", "")).strip()
    solution = str(record.get("solution", "")).strip()
    answer = _extract_boxed_text(solution) or _extract_math_answer_from_solution(solution)
    level = str(record.get("level", "")).strip()
    math_type = str(record.get("type", config_name)).strip()
    prompt_lines = [
        "Solve the following competition mathematics problem.",
        problem,
        "",
        "Return JSON with:",
        "- output.final_answer: the final exact answer only",
        "- output.solution_outline: concise reasoning outline",
        "- confidence: 0..1",
        "- summary: short string",
        "If the answer is naturally written in LaTeX, return the mathematical content only without surrounding prose.",
    ]
    return {
        "id": f"{config_name}:{record_index:04d}",
        "question": problem,
        "prompt": "\n".join(prompt_lines),
        "solution": solution,
        "gold_answer": answer,
        "level": level,
        "type": math_type,
        "source_config": config_name,
        "source_index": record_index,
    }


def parse_math_answer(raw_prediction: Any) -> str:
    candidates: list[str] = []
    if isinstance(raw_prediction, Mapping):
        for key in ("final_answer", "corrected_answer", "answer", "answer_text", "result", "text"):
            value = raw_prediction.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                candidates.extend(str(item) for item in value if str(item).strip())
            else:
                candidates.append(str(value))
    elif raw_prediction is not None:
        candidates.append(str(raw_prediction))

    for candidate in candidates:
        boxed = _extract_boxed_text(candidate)
        if boxed:
            return boxed

    for candidate in candidates:
        stripped = candidate.strip()
        if not stripped:
            continue
        assignment_match = re.match(
            r"^(?:[A-Za-z][A-Za-z0-9_]*\s*)=\s*(.+)$",
            stripped,
        )
        if assignment_match:
            rhs = assignment_match.group(1).strip().rstrip(".")
            if rhs:
                return rhs

    for candidate in reversed(candidates):
        stripped = candidate.strip()
        if stripped:
            lines = [line.strip() for line in stripped.splitlines() if line.strip()]
            if lines:
                return lines[-1]
    return ""


def grade_math_prediction(raw_prediction: Any, sample: Mapping[str, Any]) -> dict[str, Any]:
    prediction = parse_math_answer(raw_prediction)
    gold_answer = str(sample.get("gold_answer", "")).strip()
    return {
        "prediction_answer": prediction,
        "gold_answer": gold_answer,
        "correct": _math_equal(prediction, gold_answer),
    }


def prepare_humaneval_dataset(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "humaneval":
        raise BenchmarkSetupError("Benchmark config kind must be 'humaneval'")

    data_source = str(settings.get("data_source", "huggingface")).strip().lower()
    dataset_id = str(settings.get("dataset_id", "openai/openai_humaneval"))
    source_split = str(settings.get("source_split", "test"))
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/humaneval/processed"))).resolve()
    records_path = processed_dir / "records.jsonl"
    smoke_indices_path = processed_dir / "smoke_indices.json"
    validation_indices_path = processed_dir / "validation_indices.json"
    test_indices_path = processed_dir / "test_indices.json"
    metadata_path = processed_dir / "dataset_manifest.json"
    smoke_count = _coerce_desired_count(settings.get("smoke_count", 20))
    split_seed = int(settings.get("split_seed", 42))
    validation_fraction = float(settings.get("validation_fraction", 0.2))

    processed_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(processed_dir / ".prepare.lock"):
        cached_manifest = _load_generic_cached_manifest(
            metadata_path,
            required_fields={
                "dataset_id": dataset_id,
                "data_source": data_source,
                "source_split": source_split,
                "split_seed": split_seed,
                "validation_fraction": validation_fraction,
            },
        )
        if cached_manifest is not None:
            upstream_manifest = _prepare_optional_upstream_manifest(
                _prepare_humaneval_upstream_assets,
                settings,
                dataset_id=dataset_id,
                split_seed=split_seed,
            )
            cached_manifest = dict(cached_manifest)
            if upstream_manifest is not None:
                cached_manifest["upstream_full_manifest_path"] = str(Path(upstream_manifest["manifest_path"]).resolve())
                cached_manifest["upstream_train_available"] = bool(upstream_manifest.get("train_available", False))
                cached_manifest["upstream_train_sample_count"] = int(upstream_manifest.get("train_sample_count", 0))
            _write_json(metadata_path, cached_manifest)
            return cached_manifest

        if data_source == "aflow_package":
            archive_path = _prepare_aflow_public_benchmark_archive(settings)
            validation_source_records = _read_aflow_jsonl_records(archive_path, "humaneval_validate.jsonl")
            test_source_records = _read_aflow_jsonl_records(archive_path, "humaneval_test.jsonl")
            public_test_records = _read_aflow_jsonl_records(archive_path, "humaneval_public_test.jsonl")
            public_tests_by_task_id = {
                str(item.get("problem_id") or item.get("task_id") or "").strip(): _normalize_humaneval_public_tests(
                    item.get("test", [])
                )
                for item in public_test_records
                if str(item.get("problem_id") or item.get("task_id") or "").strip()
            }
            validation_records = [
                {
                    **normalize_humaneval_record(
                        item,
                        record_index=index,
                        public_tests=public_tests_by_task_id.get(
                            str(item.get("task_id", f"HumanEval/{index}")).strip(),
                            [],
                        ),
                    ),
                    "source_split": "validation",
                }
                for index, item in enumerate(validation_source_records)
            ]
            test_records = [
                {
                    **normalize_humaneval_record(
                        item,
                        record_index=index,
                        public_tests=public_tests_by_task_id.get(
                            str(item.get("task_id", f"HumanEval/{index}")).strip(),
                            [],
                        ),
                    ),
                    "source_split": "test",
                }
                for index, item in enumerate(test_source_records)
            ]
            records = [*validation_records, *test_records]
            validation_indices = list(range(len(validation_records)))
            test_indices = list(range(len(validation_records), len(records)))
        else:
            datasets_module = _import_datasets_module()
            dataset = datasets_module.load_dataset(dataset_id, split=source_split)
            records = [normalize_humaneval_record(item, record_index=index) for index, item in enumerate(dataset)]
            validation_indices, test_indices = _split_validation_test_indices(
                len(records),
                validation_fraction=validation_fraction,
                seed=split_seed,
            )

        _write_jsonl(records_path, records)
        smoke_source = validation_indices if validation_indices else test_indices
        smoke_goal = len(smoke_source) if smoke_count is None else min(smoke_count, len(smoke_source))
        smoke_indices = smoke_source[:smoke_goal]
        _write_json(smoke_indices_path, smoke_indices)
        _write_json(validation_indices_path, validation_indices)
        _write_json(test_indices_path, test_indices)

        manifest = {
            "dataset_id": dataset_id,
            "data_source": data_source,
            "source_split": source_split,
            "record_count": len(records),
            "smoke_count": len(smoke_indices),
            "validation_count": len(validation_indices),
            "test_count": len(test_indices),
            "split_seed": split_seed,
            "validation_fraction": validation_fraction,
            "records_path": str(records_path),
            "smoke_indices_path": str(smoke_indices_path),
            "validation_indices_path": str(validation_indices_path),
            "test_indices_path": str(test_indices_path),
            "metric": "pass@1",
            "paper_reference_subset": "AFlow HumanEval public set",
            "public_test_records_available": sum(
                1 for record in records if record.get("public_tests")
            ),
        }
        if data_source == "aflow_package":
            manifest["aflow_archive_path"] = str(archive_path)
        upstream_manifest = _prepare_optional_upstream_manifest(
            _prepare_humaneval_upstream_assets,
            settings,
            dataset_id=dataset_id,
            split_seed=split_seed,
        )
        if upstream_manifest is not None:
            manifest["upstream_full_manifest_path"] = str(Path(upstream_manifest["manifest_path"]).resolve())
            manifest["upstream_train_available"] = bool(upstream_manifest.get("train_available", False))
            manifest["upstream_train_sample_count"] = int(upstream_manifest.get("train_sample_count", 0))
        _write_json(metadata_path, manifest)
        return manifest


def _normalize_humaneval_public_tests(raw_tests: Any) -> list[str]:
    if raw_tests is None:
        return []
    if isinstance(raw_tests, str):
        candidate = raw_tests.strip()
        return [candidate] if candidate else []
    if isinstance(raw_tests, Sequence):
        normalized = [str(item).strip() for item in raw_tests if str(item).strip()]
        return normalized
    return [str(raw_tests).strip()] if str(raw_tests).strip() else []


def _extract_humaneval_assertions_from_test(test: str) -> list[str]:
    assertions: list[str] = []
    for line in test.splitlines():
        stripped = line.strip()
        if stripped.startswith("assert "):
            assertions.append(stripped)
    return assertions


def normalize_humaneval_record(
    record: Mapping[str, Any],
    *,
    record_index: int,
    public_tests: Sequence[str] | None = None,
) -> dict[str, Any]:
    task_id = str(record.get("task_id", f"HumanEval/{record_index}")).strip()
    prompt = str(record.get("prompt", "")).rstrip()
    entry_point = str(record.get("entry_point", "")).strip()
    prompt_lines = [
        "Write a correct Python solution for the following HumanEval task.",
        "Return JSON with:",
        "- output.completion: complete Python code containing the function definition",
        "- output.notes: brief notes about the approach",
        "- confidence: 0..1",
        "- summary: short string",
        "",
        prompt,
    ]
    return {
        "id": task_id,
        "task_id": task_id,
        "prompt": prompt,
        "entry_point": entry_point,
        "test": str(record.get("test", "")),
        "canonical_solution": str(record.get("canonical_solution", "")),
        "public_tests": list(public_tests or _normalize_humaneval_public_tests(record.get("public_tests"))),
        "question": prompt,
        "model_prompt": "\n".join(prompt_lines),
    }


def extract_humaneval_completion(raw_prediction: Any) -> str:
    candidates: list[str] = []
    if isinstance(raw_prediction, Mapping):
        for key in ("completion", "corrected_completion", "code", "final_code", "answer", "text"):
            value = raw_prediction.get(key)
            if value is None:
                continue
            candidates.append(str(value))
    elif raw_prediction is not None:
        candidates.append(str(raw_prediction))

    for candidate in candidates:
        code = _extract_code_block(candidate) or candidate.strip()
        if code:
            return code.strip()
    return ""


def grade_humaneval_prediction(
    raw_prediction: Any,
    sample: Mapping[str, Any],
    *,
    python_executable: str | None = None,
    timeout_s: int = 15,
) -> dict[str, Any]:
    completion = extract_humaneval_completion(raw_prediction)
    if not completion:
        return {
            "prediction_code": "",
            "passed": False,
            "result": "empty_completion",
            "correct": False,
        }

    status, detail = _execute_humaneval_check(
        completion=completion,
        test=str(sample.get("test", "")),
        entry_point=str(sample.get("entry_point", "")),
        python_executable=python_executable or sys.executable,
        timeout_s=timeout_s,
    )
    return {
        "prediction_code": completion,
        "passed": status == "passed",
        "result": detail,
        "correct": status == "passed",
    }


def run_humaneval_public_tests(
    raw_prediction: Any,
    sample: Mapping[str, Any],
    *,
    python_executable: str | None = None,
    timeout_s: int = 15,
) -> dict[str, Any]:
    completion = extract_humaneval_completion(raw_prediction)
    if not completion:
        return {
            "prediction_code": "",
            "public_passed": False,
            "public_result": "empty_completion",
            "public_test_count": len(_normalize_humaneval_public_tests(sample.get("public_tests"))),
        }

    public_tests = _normalize_humaneval_public_tests(sample.get("public_tests"))
    if not public_tests:
        public_tests = _extract_humaneval_assertions_from_test(str(sample.get("test", "")))
    if not public_tests:
        return {
            "prediction_code": completion,
            "public_passed": True,
            "public_result": "no_public_tests",
            "public_test_count": 0,
        }

    status, detail = _execute_humaneval_assertions(
        completion=completion,
        assertions=public_tests,
        entry_point=str(sample.get("entry_point", "")),
        python_executable=python_executable or sys.executable,
        timeout_s=timeout_s,
    )
    return {
        "prediction_code": completion,
        "public_passed": status == "passed",
        "public_result": detail,
        "public_test_count": len(public_tests),
    }


def probe_humaneval_call(
    *,
    completion: str,
    failing_call: str,
    entry_point: str,
    python_executable: str | None = None,
    timeout_s: int = 15,
) -> dict[str, str]:
    call_expr = str(failing_call or "").strip()
    if not completion.strip() or not call_expr:
        return {
            "actual_repr": "",
            "probe_status": "missing_probe",
            "probe_message": "",
        }

    support_code = _humaneval_support_prelude(entry_point)
    harness = (
        "import hashlib\n"
        "import math\n"
        "import re\n"
        "from typing import Any, Dict, List, Optional, Tuple\n\n"
        f"{support_code}"
        f"{completion.strip()}\n\n"
        "candidate = " + entry_point + "\n"
        "try:\n"
        f"    _dynaforge_probe_value = {call_expr}\n"
        "    print(repr(_dynaforge_probe_value))\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__ + ': ' + str(exc))\n"
        "    raise\n"
    )
    with tempfile.TemporaryDirectory(prefix="dynaforge_humaneval_probe_") as tmp_dir:
        script_path = Path(tmp_dir) / "probe.py"
        script_path.write_text(harness, encoding="utf-8")
        try:
            completed = subprocess.run(
                [python_executable or sys.executable, str(script_path)],
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "actual_repr": "",
                "probe_status": "timeout",
                "probe_message": "probe execution timed out",
            }
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode == 0:
        actual_repr = stdout.splitlines()[-1].strip() if stdout else ""
        return {
            "actual_repr": actual_repr,
            "probe_status": "ok",
            "probe_message": "",
        }
    detail = stderr or stdout or f"returncode={completed.returncode}"
    return {
        "actual_repr": "",
        "probe_status": "failed",
        "probe_message": detail,
    }


def prepare_medqa_dataset(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "medqa":
        raise BenchmarkSetupError("Benchmark config kind must be 'medqa'")

    dataset_id = str(settings.get("dataset_id", "bigbio/med_qa"))
    trust_remote_code = bool(settings.get("trust_remote_code", True))
    requested_config = settings.get("config_name")
    requested_split = settings.get("split")
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/medqa/processed"))).resolve()
    records_path = processed_dir / "records.jsonl"
    smoke_indices_path = processed_dir / "smoke_indices.json"
    full_indices_path = processed_dir / "full_indices.json"
    metadata_path = processed_dir / "dataset_manifest.json"
    smoke_count = _coerce_desired_count(settings.get("smoke_count", 5))
    full_count = _coerce_desired_count(settings.get("full_count", 50))
    seed = int(settings.get("seed", 0))
    split_candidates = _ordered_unique([str(requested_split) if requested_split else None, "test", "validation", "train"])

    cached_manifest_hint = _load_cached_json(metadata_path)
    if cached_manifest_hint and str(cached_manifest_hint.get("dataset_id", "")) == dataset_id:
        cached_manifest = _load_cached_medqa_manifest(
            metadata_path=metadata_path,
            dataset_id=dataset_id,
            chosen_config=str(cached_manifest_hint.get("chosen_config", "")),
            split_candidates=split_candidates,
            seed=seed,
        )
        if cached_manifest is not None:
            return cached_manifest

    datasets_module = _import_datasets_module()

    config_names = list(
        datasets_module.get_dataset_config_names(
            dataset_id,
            trust_remote_code=trust_remote_code,
        )
    )
    if not config_names:
        raise BenchmarkSetupError(f"No dataset configs found for {dataset_id}")

    chosen_config = str(requested_config) if requested_config in config_names else _choose_medqa_config(config_names)

    processed_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(processed_dir / ".prepare.lock"):
        cached_manifest = _load_cached_medqa_manifest(
            metadata_path=metadata_path,
            dataset_id=dataset_id,
            chosen_config=chosen_config,
            split_candidates=split_candidates,
            seed=seed,
        )
        if cached_manifest is not None:
            all_splits_manifest = _prepare_medqa_all_splits_assets(
                settings,
                datasets_module=datasets_module,
                dataset_id=dataset_id,
                chosen_config=chosen_config,
                trust_remote_code=trust_remote_code,
                seed=seed,
            )
            cached_manifest = dict(cached_manifest)
            cached_manifest["all_splits_manifest_path"] = str(Path(all_splits_manifest["manifest_path"]).resolve())
            cached_manifest["train_record_count"] = int(all_splits_manifest.get("split_record_counts", {}).get("train", 0))
            cached_manifest["test_record_count"] = int(all_splits_manifest.get("split_record_counts", {}).get("test", 0))
            cached_manifest["train_sample_count"] = int(all_splits_manifest.get("train_sample_count", 0))
            _write_json(metadata_path, cached_manifest)
            return cached_manifest

        chosen_split: str | None = None
        records: list[dict[str, Any]] | None = None
        last_error: Exception | None = None
        for split_name in split_candidates:
            if split_name is None:
                continue
            try:
                dataset = datasets_module.load_dataset(
                    dataset_id,
                    chosen_config,
                    split=split_name,
                    trust_remote_code=trust_remote_code,
                )
                records = [normalize_medqa_record(item) for item in dataset]
                chosen_split = split_name
                break
            except Exception as exc:  # pragma: no cover - exercised in live runs
                last_error = exc

        if records is None or chosen_split is None:
            raise BenchmarkSetupError(f"Unable to load any requested split for {dataset_id}: {last_error}")

        _write_jsonl(records_path, records)
        full_indices = _sample_indices(len(records), full_count, seed=seed)
        smoke_goal = len(full_indices) if smoke_count is None else min(smoke_count, len(full_indices))
        smoke_indices = full_indices[:smoke_goal]
        _write_json(full_indices_path, full_indices)
        _write_json(smoke_indices_path, smoke_indices)

        manifest = {
            "dataset_id": dataset_id,
            "chosen_config": chosen_config,
            "chosen_split": chosen_split,
            "record_count": len(records),
            "smoke_count": len(smoke_indices),
            "full_count": len(full_indices),
            "seed": seed,
            "records_path": str(records_path),
            "smoke_indices_path": str(smoke_indices_path),
            "full_indices_path": str(full_indices_path),
        }
        all_splits_manifest = _prepare_medqa_all_splits_assets(
            settings,
            datasets_module=datasets_module,
            dataset_id=dataset_id,
            chosen_config=chosen_config,
            trust_remote_code=trust_remote_code,
            seed=seed,
            preloaded_split_name=chosen_split,
            preloaded_records=records,
        )
        manifest["all_splits_manifest_path"] = str(Path(all_splits_manifest["manifest_path"]).resolve())
        manifest["train_record_count"] = int(all_splits_manifest.get("split_record_counts", {}).get("train", 0))
        manifest["test_record_count"] = int(all_splits_manifest.get("split_record_counts", {}).get("test", 0))
        manifest["train_sample_count"] = int(all_splits_manifest.get("train_sample_count", 0))
        _write_json(metadata_path, manifest)
        return manifest


def normalize_medqa_record(record: Mapping[str, Any]) -> dict[str, Any]:
    choices = [str(choice) for choice in record.get("choices", [])]
    labeled_choices = [{"label": _OPTION_LABELS[index], "text": choice} for index, choice in enumerate(choices)]
    answer_text = _extract_medqa_answer_text(record.get("answer"))
    answer_label = ""
    for option in labeled_choices:
        if _normalize_text(option["text"]) == _normalize_text(answer_text):
            answer_label = option["label"]
            break

    question = str(record.get("question", "")).strip()
    prompt_lines = [f"Question: {question}", "Options:"]
    prompt_lines.extend(f"{option['label']}. {option['text']}" for option in labeled_choices)
    prompt_lines.append("Return the single best answer as JSON with final_answer_label and final_answer_text.")

    return {
        "id": str(record.get("id", record.get("question_id", ""))),
        "question_id": str(record.get("question_id", record.get("id", ""))),
        "question": question,
        "prompt": "\n".join(prompt_lines),
        "options": labeled_choices,
        "gold_answer_label": answer_label,
        "gold_answer_text": answer_text,
        "metadata": {
            "document_id": str(record.get("document_id", "")),
            "type": str(record.get("type", "")),
            "context": str(record.get("context", "")),
        },
    }


def parse_medqa_answer(raw_prediction: Any, options: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    labeled_options = [
        {"label": str(option.get("label", "")).strip().upper(), "text": str(option.get("text", "")).strip()}
        for option in options
    ]
    valid_labels = {option["label"] for option in labeled_options if option["label"]}
    normalized_options = {_normalize_text(option["text"]): option["label"] for option in labeled_options if option["text"]}
    label_candidate = ""
    text_candidate = ""

    candidates: list[str] = []
    if isinstance(raw_prediction, Mapping):
        for key in ("final_answer_label", "corrected_answer_label", "answer_label", "choice", "label"):
            value = raw_prediction.get(key)
            if value is None:
                continue
            if isinstance(value, Mapping):
                nested = parse_medqa_answer(value, options)
                if nested["label"] or nested["text"]:
                    return nested
            elif isinstance(value, (list, tuple)):
                candidates.extend(str(item) for item in value)
            else:
                candidate = str(value)
                candidates.append(candidate)
                stripped = candidate.strip().upper()
                if not label_candidate and stripped in valid_labels:
                    label_candidate = stripped
        for key in ("final_answer_text", "corrected_answer_text", "answer_text", "answer", "result", "text"):
            value = raw_prediction.get(key)
            if value is None:
                continue
            if isinstance(value, Mapping):
                nested = parse_medqa_answer(value, options)
                if nested["label"] or nested["text"]:
                    return nested
            elif isinstance(value, (list, tuple)):
                joined = " ".join(str(item) for item in value).strip()
                if joined:
                    text_candidate = joined
                    candidates.append(joined)
            else:
                text_candidate = str(value).strip()
                if text_candidate:
                    candidates.append(text_candidate)
    elif raw_prediction is not None:
        text_candidate = str(raw_prediction).strip()
        candidates.append(text_candidate)

    for candidate in candidates:
        stripped = candidate.strip()
        upper = stripped.upper()
        if upper in valid_labels:
            label_candidate = upper
            break
        match = re.search(r"\b([A-Z])\b", upper)
        if match and match.group(1) in valid_labels:
            label_candidate = match.group(1)
            break

    text_label, text_match_text = _match_medqa_option_text(text_candidate, labeled_options)
    if text_label and (not label_candidate or label_candidate != text_label):
        return {"label": text_label, "text": text_match_text}

    if label_candidate:
        return {"label": label_candidate, "text": _lookup_option_text(label_candidate, labeled_options)}

    normalized_candidates = [_normalize_text(candidate) for candidate in candidates if candidate.strip()]
    for normalized_candidate in normalized_candidates:
        if normalized_candidate in normalized_options:
            label = normalized_options[normalized_candidate]
            return {"label": label, "text": _lookup_option_text(label, labeled_options)}
        for normalized_text, label in normalized_options.items():
            if normalized_text and normalized_text in normalized_candidate:
                return {"label": label, "text": _lookup_option_text(label, labeled_options)}

    if text_label:
        return {"label": text_label, "text": text_match_text}

    return {"label": "", "text": candidates[0].strip() if candidates else ""}


def grade_medqa_prediction(raw_prediction: Any, sample: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_medqa_answer(raw_prediction, sample.get("options", []))
    gold_label = str(sample.get("gold_answer_label", "")).strip().upper()
    gold_text = str(sample.get("gold_answer_text", "")).strip()
    return {
        "prediction_label": parsed["label"],
        "prediction_text": parsed["text"],
        "gold_label": gold_label,
        "gold_text": gold_text,
        "correct": bool(parsed["label"]) and parsed["label"] == gold_label,
    }


def _prepare_medqa_all_splits_assets(
    settings: Mapping[str, Any],
    *,
    datasets_module: Any,
    dataset_id: str,
    chosen_config: str,
    trust_remote_code: bool,
    seed: int,
    preloaded_split_name: str | None = None,
    preloaded_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/medqa/processed"))).resolve()
    all_splits_dir = Path(str(settings.get("all_splits_dir", processed_dir.parent / "all_splits"))).resolve()
    metadata_path = all_splits_dir / "dataset_manifest.json"
    train_sample_count = _coerce_desired_count(settings.get("train_sample_count", 1000))
    train_sample_seed = int(settings.get("train_sample_seed", seed))
    split_names = [
        str(item)
        for item in _ordered_unique(
            _get_dataset_split_names(
                datasets_module,
                dataset_id,
                chosen_config,
                trust_remote_code=trust_remote_code,
            )
        )
        if item is not None
    ]

    all_splits_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(all_splits_dir / ".prepare.lock"):
        cached = _load_cached_split_manifest(
            metadata_path,
            required_fields={
                "dataset_id": dataset_id,
                "chosen_config": chosen_config,
                "available_splits": split_names,
                "train_sample_requested_count": train_sample_count,
                "train_sample_seed": train_sample_seed,
            },
        )
        if cached is not None:
            return cached

        split_record_counts: dict[str, int] = {}
        split_record_paths: dict[str, str] = {}
        train_sample_indices: list[int] = []
        train_sample_records: list[dict[str, Any]] = []

        for split_name in split_names:
            if split_name == preloaded_split_name and preloaded_records is not None:
                records = [dict(item) for item in preloaded_records]
            else:
                dataset = datasets_module.load_dataset(
                    dataset_id,
                    chosen_config,
                    split=split_name,
                    trust_remote_code=trust_remote_code,
                )
                records = [normalize_medqa_record(item) for item in dataset]
            records_path = all_splits_dir / f"{split_name}_records.jsonl"
            _write_jsonl(records_path, records)
            split_record_counts[split_name] = len(records)
            split_record_paths[split_name] = str(records_path)
            if split_name == "train":
                train_sample_indices = _sample_indices(len(records), train_sample_count, seed=train_sample_seed)
                train_sample_records = [records[index] for index in train_sample_indices]

        train_sample_indices_path = all_splits_dir / "train_sample_indices.json"
        train_sample_records_path = all_splits_dir / "train_sample_records.jsonl"
        _write_json(train_sample_indices_path, train_sample_indices)
        _write_jsonl(train_sample_records_path, train_sample_records)

        manifest = {
            "manifest_path": str(metadata_path),
            "dataset_id": dataset_id,
            "chosen_config": chosen_config,
            "available_splits": split_names,
            "split_record_counts": split_record_counts,
            "split_record_paths": split_record_paths,
            "train_records_path": split_record_paths.get("train", ""),
            "test_records_path": split_record_paths.get("test", ""),
            "validation_records_path": split_record_paths.get("validation", ""),
            "train_sample_requested_count": train_sample_count,
            "train_sample_seed": train_sample_seed,
            "train_sample_count": len(train_sample_indices),
            "train_sample_indices_path": str(train_sample_indices_path),
            "train_sample_records_path": str(train_sample_records_path),
        }
        _write_json(metadata_path, manifest)
        return manifest


def _prepare_optional_upstream_manifest(loader: Any, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
    try:
        return loader(*args, **kwargs)
    except Exception:
        return None


def _prepare_math_upstream_assets(
    settings: Mapping[str, Any],
    *,
    dataset_id: str,
    split_seed: int,
) -> dict[str, Any]:
    datasets_module = _import_datasets_module()
    config_names = list(settings.get("config_names") or datasets_module.get_dataset_config_names(dataset_id))
    selected_types = [str(item).strip() for item in settings.get("selected_types", _AFLOW_MATH_TYPES)]
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/math/processed"))).resolve()
    upstream_dir = Path(str(settings.get("upstream_dir", processed_dir.parent / "upstream_full"))).resolve()
    metadata_path = upstream_dir / "dataset_manifest.json"
    train_sample_count = _coerce_desired_count(settings.get("train_sample_count", 1000))
    train_sample_seed = int(settings.get("train_sample_seed", split_seed))

    split_names = _ordered_unique(
        split_name
        for config_name in config_names
        for split_name in _get_dataset_split_names(datasets_module, dataset_id, config_name)
    )
    available_splits = [str(item) for item in split_names if item is not None]

    upstream_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(upstream_dir / ".prepare.lock"):
        cached = _load_cached_split_manifest(
            metadata_path,
            required_fields={
                "dataset_id": dataset_id,
                "config_names": config_names,
                "selected_types": selected_types,
                "available_splits": available_splits,
                "train_sample_requested_count": train_sample_count,
                "train_sample_seed": train_sample_seed,
            },
        )
        if cached is not None:
            return cached

        split_record_counts: dict[str, int] = {}
        split_record_paths: dict[str, str] = {}
        train_sample_indices: list[int] = []
        train_sample_records: list[dict[str, Any]] = []

        for split_name in available_splits:
            split_records: list[dict[str, Any]] = []
            for config_name in config_names:
                if split_name not in _get_dataset_split_names(datasets_module, dataset_id, config_name):
                    continue
                dataset = datasets_module.load_dataset(dataset_id, config_name, split=split_name)
                for record_index, item in enumerate(dataset):
                    normalized = normalize_math_record(item, config_name=config_name, record_index=record_index)
                    normalized["source_split"] = split_name
                    split_records.append(normalized)
            split_records.sort(key=lambda item: str(item["id"]))
            records_path = upstream_dir / f"{split_name}_records.jsonl"
            _write_jsonl(records_path, split_records)
            split_record_counts[split_name] = len(split_records)
            split_record_paths[split_name] = str(records_path)
            if split_name == "train":
                train_sample_indices = _sample_indices(len(split_records), train_sample_count, seed=train_sample_seed)
                train_sample_records = [split_records[index] for index in train_sample_indices]

        train_sample_indices_path = upstream_dir / "train_sample_indices.json"
        train_sample_records_path = upstream_dir / "train_sample_records.jsonl"
        _write_json(train_sample_indices_path, train_sample_indices)
        _write_jsonl(train_sample_records_path, train_sample_records)

        manifest = {
            "manifest_path": str(metadata_path),
            "dataset_id": dataset_id,
            "config_names": config_names,
            "selected_types": selected_types,
            "available_splits": available_splits,
            "split_record_counts": split_record_counts,
            "split_record_paths": split_record_paths,
            "train_count": split_record_counts.get("train", 0),
            "test_count": split_record_counts.get("test", 0),
            "train_records_path": split_record_paths.get("train", ""),
            "test_records_path": split_record_paths.get("test", ""),
            "train_sample_requested_count": train_sample_count,
            "train_sample_seed": train_sample_seed,
            "train_sample_count": len(train_sample_indices),
            "train_sample_indices_path": str(train_sample_indices_path),
            "train_sample_records_path": str(train_sample_records_path),
            "aflow_eval_alignment": True,
            "note": "Canonical benchmark validation/test remains the packaged AFlow split; upstream full splits are downloaded for train/test completeness and train sampling.",
        }
        _write_json(metadata_path, manifest)
        return manifest


def _prepare_humaneval_upstream_assets(
    settings: Mapping[str, Any],
    *,
    dataset_id: str,
    split_seed: int,
) -> dict[str, Any]:
    datasets_module = _import_datasets_module()
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/humaneval/processed"))).resolve()
    upstream_dir = Path(str(settings.get("upstream_dir", processed_dir.parent / "upstream_full"))).resolve()
    metadata_path = upstream_dir / "dataset_manifest.json"
    train_sample_count = _coerce_desired_count(settings.get("train_sample_count", 1000))
    train_sample_seed = int(settings.get("train_sample_seed", split_seed))
    available_splits = [
        str(item)
        for item in _ordered_unique(_get_dataset_split_names(datasets_module, dataset_id))
        if item is not None
    ]

    upstream_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(upstream_dir / ".prepare.lock"):
        cached = _load_cached_split_manifest(
            metadata_path,
            required_fields={
                "dataset_id": dataset_id,
                "available_splits": available_splits,
                "train_sample_requested_count": train_sample_count,
                "train_sample_seed": train_sample_seed,
            },
        )
        if cached is not None:
            return cached

        split_record_counts: dict[str, int] = {}
        split_record_paths: dict[str, str] = {}
        train_sample_indices: list[int] = []
        train_sample_records: list[dict[str, Any]] = []

        for split_name in available_splits:
            dataset = datasets_module.load_dataset(dataset_id, split=split_name)
            records = [normalize_humaneval_record(item, record_index=index) for index, item in enumerate(dataset)]
            records_path = upstream_dir / f"{split_name}_records.jsonl"
            _write_jsonl(records_path, records)
            split_record_counts[split_name] = len(records)
            split_record_paths[split_name] = str(records_path)
            if split_name == "train":
                train_sample_indices = _sample_indices(len(records), train_sample_count, seed=train_sample_seed)
                train_sample_records = [records[index] for index in train_sample_indices]

        train_sample_indices_path = upstream_dir / "train_sample_indices.json"
        train_sample_records_path = upstream_dir / "train_sample_records.jsonl"
        _write_json(train_sample_indices_path, train_sample_indices)
        _write_jsonl(train_sample_records_path, train_sample_records)

        manifest = {
            "manifest_path": str(metadata_path),
            "dataset_id": dataset_id,
            "available_splits": available_splits,
            "split_record_counts": split_record_counts,
            "split_record_paths": split_record_paths,
            "train_available": "train" in split_record_counts,
            "train_count": split_record_counts.get("train", 0),
            "test_count": split_record_counts.get("test", 0),
            "train_records_path": split_record_paths.get("train", ""),
            "test_records_path": split_record_paths.get("test", ""),
            "train_sample_requested_count": train_sample_count,
            "train_sample_seed": train_sample_seed,
            "train_sample_count": len(train_sample_indices),
            "train_sample_indices_path": str(train_sample_indices_path),
            "train_sample_records_path": str(train_sample_records_path),
            "aflow_eval_alignment": True,
            "note": "Canonical benchmark validation/test remains the packaged AFlow split. The upstream official dataset currently exposes only the recorded available splits.",
        }
        _write_json(metadata_path, manifest)
        return manifest


def prepare_scanpy_pbmc3k_case_study(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "case_study":
        raise BenchmarkSetupError("Benchmark config kind must be 'case_study'")
    if str(settings.get("case_id", "")) != "scanpy_pbmc3k_umap":
        raise BenchmarkSetupError("Unsupported case-study id for this helper")

    from dynaforge.case_studies.scanpy_pbmc3k_job import DEFAULT_ANALYSIS_CONFIG

    case_root = Path(str(settings.get("case_root", "benchmarks/case_studies/scanpy_pbmc3k"))).resolve()
    repos_root = Path(str(settings.get("repo_root", "external/case_studies/scanpy_pbmc3k/repos"))).resolve()
    methods_path = case_root / "materials" / "methods_excerpt.txt"
    target_desc_path = case_root / "materials" / "target_figure_description.txt"
    candidate_repo_path = case_root / "candidate_repos.json"
    task_spec_path = case_root / "task_spec.json"
    data_manifest_path = case_root / "data_manifest.json"
    reference_dir = case_root / "reference"
    generated_dir = case_root / "generated"
    data_dir = case_root / "data"
    for path in (reference_dir, generated_dir, data_dir, methods_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    methods_excerpt = str(
        settings.get(
            "methods_excerpt",
            (
                "Filter PBMC3k cells with fewer than 200 genes and genes seen in fewer than 3 cells. "
                "Normalize counts per cell to a target sum of 10,000, apply log1p, keep the top 2,000 highly variable genes, "
                "scale values with max_value=10, run PCA, build a 10-nearest-neighbor graph using up to 40 PCs, compute UMAP, "
                "cluster with Leiden resolution 0.5, and plot a UMAP colored by the Leiden labels."
            ),
        )
    )
    target_description = str(
        settings.get(
            "target_figure_description",
            (
                "Produce a PNG UMAP embedding of PBMC3k cells colored by Leiden clusters. "
                "The figure should have multiple visually distinct clusters and preserve a standard single-cell Scanpy style."
            ),
        )
    )
    candidate_repos = list(
        settings.get(
            "candidate_repos",
            [
                {"name": "scanpy", "url": "https://github.com/scverse/scanpy.git"},
                {"name": "scanpy-tutorials", "url": "https://github.com/scverse/scanpy-tutorials.git"},
            ],
        )
    )

    repo_records: list[dict[str, Any]] = []
    for repo in candidate_repos:
        repo_name = str(repo.get("name", "")).strip()
        repo_url = str(repo.get("url", "")).strip()
        if not repo_name or not repo_url:
            raise BenchmarkSetupError(f"Invalid candidate repo entry: {repo}")
        repo_path = repos_root / repo_name
        if (repo_path / ".git").exists():
            action = "updated"
            _run_command(["git", "-C", str(repo_path), "pull", "--ff-only"])
        else:
            action = "cloned"
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            _run_command(["git", "clone", "--depth", "1", repo_url, str(repo_path)])
        repo_records.append(
            {
                "name": repo_name,
                "url": repo_url,
                "path": str(repo_path),
                "action": action,
            }
        )

    methods_path.write_text(methods_excerpt + "\n", encoding="utf-8")
    target_desc_path.write_text(target_description + "\n", encoding="utf-8")
    _write_json(candidate_repo_path, repo_records)

    raw_data_path = data_dir / "pbmc3k_raw.h5ad"
    reference_figure_path = reference_dir / "reference_umap.png"
    reference_summary_path = reference_dir / "reference_summary.json"
    generated_figure_path = generated_dir / "generated_umap.png"
    generated_summary_path = generated_dir / "generated_summary.json"

    task_spec = {
        "case_id": "scanpy_pbmc3k_umap",
        "title": str(settings.get("title", "Scanpy PBMC3k UMAP reproduction")),
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "reference_figure_path": str(reference_figure_path),
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "default_generated_figure_path": str(generated_figure_path),
        "default_generated_summary_path": str(generated_summary_path),
        "default_analysis_config": DEFAULT_ANALYSIS_CONFIG,
    }
    _write_json(task_spec_path, task_spec)

    manifest = {
        "case_id": "scanpy_pbmc3k_umap",
        "case_root": str(case_root),
        "repo_root": str(repos_root),
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "task_spec_path": str(task_spec_path),
        "raw_data_path": str(raw_data_path),
        "reference_figure_path": str(reference_figure_path),
        "reference_summary_path": str(reference_summary_path),
        "generated_figure_path": str(generated_figure_path),
        "generated_summary_path": str(generated_summary_path),
        "candidate_repos": repo_records,
    }
    _write_json(data_manifest_path, manifest)
    return manifest


def prepare_scanpy_paul15_case_study(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "case_study":
        raise BenchmarkSetupError("Benchmark config kind must be 'case_study'")
    case_id = str(settings.get("case_id", ""))
    if case_id not in {
        "scanpy_paul15_trajectory",
        "scanpy_paul15_paper_figure",
        "scanpy_paul15_specialized_collaboration",
        "scanpy_moignard15_method_transfer",
        "scanpy_moignard15_specialized_collaboration",
        "scanpy_krumsiek11_method_transfer",
        "scanpy_krumsiek11_specialized_collaboration",
    }:
        raise BenchmarkSetupError("Unsupported case-study id for this helper")
    collaboration_case = case_id in {
        "scanpy_paul15_specialized_collaboration",
        "scanpy_moignard15_specialized_collaboration",
        "scanpy_krumsiek11_specialized_collaboration",
    }
    transfer_case = case_id in {
        "scanpy_moignard15_method_transfer",
        "scanpy_moignard15_specialized_collaboration",
        "scanpy_krumsiek11_method_transfer",
        "scanpy_krumsiek11_specialized_collaboration",
    }
    paper_figure_case = case_id in {
        "scanpy_paul15_paper_figure",
        "scanpy_paul15_specialized_collaboration",
        "scanpy_moignard15_method_transfer",
        "scanpy_moignard15_specialized_collaboration",
        "scanpy_krumsiek11_method_transfer",
        "scanpy_krumsiek11_specialized_collaboration",
    }
    if "krumsiek11" in case_id:
        dataset_id = "krumsiek11"
        dataset_label = "Krumsiek11"
    elif "moignard15" in case_id:
        dataset_id = "moignard15"
        dataset_label = "Moignard15"
    else:
        dataset_id = "paul15"
        dataset_label = "Paul15"

    from dynaforge.case_studies.scanpy_paul15_job import DEFAULT_ANALYSIS_CONFIG

    if case_id == "scanpy_paul15_specialized_collaboration":
        default_case_root = "benchmarks/case_studies/scanpy_paul15_collaboration"
    elif case_id == "scanpy_paul15_paper_figure":
        default_case_root = "benchmarks/case_studies/scanpy_paul15_paper_figure"
    elif case_id == "scanpy_moignard15_specialized_collaboration":
        default_case_root = "benchmarks/case_studies/scanpy_moignard15_collaboration"
    elif case_id == "scanpy_moignard15_method_transfer":
        default_case_root = "benchmarks/case_studies/scanpy_moignard15_transfer"
    elif case_id == "scanpy_krumsiek11_specialized_collaboration":
        default_case_root = "benchmarks/case_studies/scanpy_krumsiek11_collaboration"
    elif case_id == "scanpy_krumsiek11_method_transfer":
        default_case_root = "benchmarks/case_studies/scanpy_krumsiek11_transfer"
    else:
        default_case_root = "benchmarks/case_studies/scanpy_paul15"
    case_root = Path(str(settings.get("case_root", default_case_root))).resolve()
    repos_root = Path(str(settings.get("repo_root", "external/case_studies/scanpy_paul15/repos"))).resolve()
    methods_path = case_root / "materials" / "methods_excerpt.txt"
    target_desc_path = case_root / "materials" / "target_figure_description.txt"
    candidate_repo_path = case_root / "candidate_repos.json"
    task_spec_path = case_root / "task_spec.json"
    data_manifest_path = case_root / "data_manifest.json"
    reference_dir = case_root / "reference"
    generated_dir = case_root / "generated"
    data_dir = case_root / "data"
    for path in (reference_dir, generated_dir, data_dir, methods_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    default_transfer_excerpt = (
        f"Transfer the Paul15-style hematopoiesis trajectory method to the public {dataset_label} dataset: "
        "run PCA and a neighborhood graph, cluster cells, infer coarse lineage structure with PAGA, "
        "compute diffusion-based pseudotime from the dataset-default root cell when available, and produce a paper-style "
        "figure package with a trajectory embedding, a PAGA graph, and a dynamic-gene evidence panel linked to pseudotime progression. "
        "Preserve dataset-native metadata and avoid source-dataset assumptions that do not hold on the target dataset."
    )
    default_source_excerpt = (
        f"Load the public {dataset_label} hematopoiesis dataset, normalize counts, log-transform, select highly variable genes, "
        "run PCA and a neighborhood graph, cluster cells, infer coarse lineage structure with PAGA, compute diffusion-based "
        "pseudotime from an automatically selected root cell, and reproduce a paper-style figure package with a trajectory embedding, "
        "a PAGA graph, and a dynamic-gene evidence panel linked to pseudotime progression."
    )
    default_plain_excerpt = (
        f"Load the public {dataset_label} hematopoiesis dataset, normalize counts, log-transform, select highly variable genes, "
        "run PCA and a neighborhood graph, cluster cells, infer coarse lineage structure with PAGA, compute diffusion-based "
        "pseudotime from an automatically selected root cell, and produce both a trajectory embedding and a PAGA graph."
    )
    methods_excerpt = str(
        settings.get(
            "methods_excerpt",
            default_plain_excerpt if not paper_figure_case else (default_transfer_excerpt if transfer_case else default_source_excerpt),
        )
    )
    default_transfer_target = (
        f"Transfer the Paul15 figure-production method to the {dataset_label} dataset and produce a paper-style figure package "
        "consisting of a trajectory figure, a PAGA graph, and a dynamic-gene panel. Preserve dataset provenance, root-selection metadata, "
        "and target-dataset group annotations in the summary."
    )
    default_source_target = (
        f"Produce a paper-style figure package for the {dataset_label} dataset consisting of a trajectory figure, a PAGA graph, "
        "and a dynamic-gene panel that shows genes whose expression varies strongly along pseudotime."
    )
    default_plain_target = (
        f"Produce a trajectory figure for the {dataset_label} dataset that shows cluster structure and pseudotime progression, "
        "plus a second PAGA graph figure summarizing connectivity between cell-state groups."
    )
    target_description = str(
        settings.get(
            "target_figure_description",
            default_plain_target if not paper_figure_case else (default_transfer_target if transfer_case else default_source_target),
        )
    )
    candidate_repos = list(
        settings.get(
            "candidate_repos",
            [
                {"name": "scanpy", "url": "https://github.com/scverse/scanpy.git"},
                {"name": "scanpy-tutorials", "url": "https://github.com/scverse/scanpy-tutorials.git"},
            ],
        )
    )

    repo_records: list[dict[str, Any]] = []
    for repo in candidate_repos:
        repo_name = str(repo.get("name", "")).strip()
        repo_url = str(repo.get("url", "")).strip()
        if not repo_name or not repo_url:
            raise BenchmarkSetupError(f"Invalid candidate repo entry: {repo}")
        repo_path = repos_root / repo_name
        if (repo_path / ".git").exists():
            action = "updated"
            _run_command(["git", "-C", str(repo_path), "pull", "--ff-only"])
        else:
            action = "cloned"
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            _run_command(["git", "clone", "--depth", "1", repo_url, str(repo_path)])
        repo_records.append(
            {
                "name": repo_name,
                "url": repo_url,
                "path": str(repo_path),
                "action": action,
            }
        )

    methods_path.write_text(methods_excerpt + "\n", encoding="utf-8")
    target_desc_path.write_text(target_description + "\n", encoding="utf-8")
    _write_json(candidate_repo_path, repo_records)

    raw_data_path = data_dir / f"{dataset_id}_raw.h5ad"
    reference_figure_path = reference_dir / "reference_trajectory.png"
    reference_paga_figure_path = reference_dir / "reference_paga.png"
    reference_gene_trend_figure_path = reference_dir / "reference_dynamic_genes.png"
    reference_summary_path = reference_dir / "reference_summary.json"
    generated_figure_path = generated_dir / "generated_trajectory.png"
    generated_paga_figure_path = generated_dir / "generated_paga.png"
    generated_gene_trend_figure_path = generated_dir / "generated_dynamic_genes.png"
    generated_summary_path = generated_dir / "generated_summary.json"

    task_spec = {
        "case_id": case_id,
        "title": str(
            settings.get(
                "title",
                "Scanpy Moignard15 specialized-agent method transfer"
                if case_id == "scanpy_moignard15_specialized_collaboration"
                else "Scanpy Krumsiek11 specialized-agent method transfer"
                if case_id == "scanpy_krumsiek11_specialized_collaboration"
                else "Scanpy Paul15 specialized-agent collaboration"
                if case_id == "scanpy_paul15_specialized_collaboration"
                else "Scanpy Moignard15 method transfer"
                if case_id == "scanpy_moignard15_method_transfer"
                else "Scanpy Krumsiek11 method transfer"
                if case_id == "scanpy_krumsiek11_method_transfer"
                else "Scanpy Paul15 paper-figure reproduction"
                if case_id == "scanpy_paul15_paper_figure"
                else "Scanpy Paul15 trajectory reconstruction",
            )
        ),
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "reference_figure_path": str(reference_figure_path),
        "reference_paga_figure_path": str(reference_paga_figure_path),
        "reference_gene_trend_figure_path": str(reference_gene_trend_figure_path) if paper_figure_case else "",
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "default_generated_figure_path": str(generated_figure_path),
        "default_generated_paga_figure_path": str(generated_paga_figure_path),
        "default_generated_gene_trend_figure_path": str(generated_gene_trend_figure_path) if paper_figure_case else "",
        "default_generated_summary_path": str(generated_summary_path),
        "default_analysis_config": {
            **DEFAULT_ANALYSIS_CONFIG,
            "dataset_id": dataset_id,
            "root_strategy": "dataset_default" if transfer_case else DEFAULT_ANALYSIS_CONFIG.get("root_strategy", "min_diffmap_1"),
        },
    }
    _write_json(task_spec_path, task_spec)

    manifest = {
        "case_id": case_id,
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "case_root": str(case_root),
        "repo_root": str(repos_root),
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "task_spec_path": str(task_spec_path),
        "raw_data_path": str(raw_data_path),
        "reference_figure_path": str(reference_figure_path),
        "reference_paga_figure_path": str(reference_paga_figure_path),
        "reference_gene_trend_figure_path": str(reference_gene_trend_figure_path) if paper_figure_case else "",
        "reference_summary_path": str(reference_summary_path),
        "generated_figure_path": str(generated_figure_path),
        "generated_paga_figure_path": str(generated_paga_figure_path),
        "generated_gene_trend_figure_path": str(generated_gene_trend_figure_path) if paper_figure_case else "",
        "generated_summary_path": str(generated_summary_path),
        "candidate_repos": repo_records,
    }
    _write_json(data_manifest_path, manifest)
    return manifest


def prepare_squidpy_visium_case_study(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "case_study":
        raise BenchmarkSetupError("Benchmark config kind must be 'case_study'")
    case_id = str(settings.get("case_id", ""))
    if case_id not in {"squidpy_visium_hne_spatial", "squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}:
        raise BenchmarkSetupError("Unsupported case-study id for this helper")
    interaction_case = case_id in {"squidpy_visium_hne_interactions", "squidpy_seqfish_method_transfer"}
    transfer_case = case_id == "squidpy_seqfish_method_transfer"
    dataset_id = "seqfish" if transfer_case else "visium_hne_adata_crop"
    dataset_label = "seqFISH" if transfer_case else "Visium H&E"

    if interaction_case:
        from dynaforge.case_studies.squidpy_visium_interactions_job import DEFAULT_ANALYSIS_CONFIG
    else:
        from dynaforge.case_studies.squidpy_visium_job import DEFAULT_ANALYSIS_CONFIG

    if case_id == "squidpy_seqfish_method_transfer":
        default_case_root = "benchmarks/case_studies/squidpy_seqfish_transfer"
    elif interaction_case:
        default_case_root = "benchmarks/case_studies/squidpy_visium_interactions"
    else:
        default_case_root = "benchmarks/case_studies/squidpy_visium_hne"
    case_root = Path(str(settings.get("case_root", default_case_root))).resolve()
    repos_root = Path(str(settings.get("repo_root", "external/case_studies/squidpy_spatial/repos"))).resolve()
    methods_path = case_root / "materials" / "methods_excerpt.txt"
    target_desc_path = case_root / "materials" / "target_figure_description.txt"
    candidate_repo_path = case_root / "candidate_repos.json"
    task_spec_path = case_root / "task_spec.json"
    data_manifest_path = case_root / "data_manifest.json"
    reference_dir = case_root / "reference"
    generated_dir = case_root / "generated"
    data_dir = case_root / "data"
    for path in (reference_dir, generated_dir, data_dir, methods_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    methods_excerpt = str(
        settings.get(
            "methods_excerpt",
            (
                "Load the public cropped Visium H&E dataset, preserve the spatial image metadata, "
                "construct a spatial-neighbor graph, and render a spatial scatter plot colored by the cluster labels "
                "on the tissue image. Keep the output faithful to a standard Squidpy spatial visualization."
                if not interaction_case
                else (
                "Load the public cropped Visium H&E dataset, preserve the spatial image metadata, "
                "construct a spatial-neighbor graph, compute neighborhood enrichment across cluster labels, "
                "and produce both a spatial scatter plot and a neighborhood-enrichment heatmap. "
                "Keep the analysis faithful to a standard Squidpy spatial-interaction workflow."
                if not transfer_case
                else
                "Transfer the Squidpy spatial-neighborhood workflow to the public seqFISH dataset. "
                "Use the published cell-type annotations in `celltype_mapped_refined`, construct spatial neighbors, "
                "compute neighborhood enrichment across those labels, and generate a spatial plot plus an interaction heatmap. "
                "Keep the analysis faithful to the official Squidpy seqFISH neighborhood-enrichment workflow."
                )
            ),
        )
    )
    target_description = str(
        settings.get(
            "target_figure_description",
            (
                "Produce a PNG spatial scatter plot for the cropped Visium H&E dataset with cluster labels overlaid "
                "on the tissue image. The figure should show a coherent spatial layout, visible tissue background, "
                "and clearly distinguishable labeled regions."
                if not interaction_case
                else (
                "Produce a PNG spatial scatter plot for the cropped Visium H&E dataset and a second PNG "
                "neighborhood-enrichment heatmap that summarizes interaction strengths between cluster labels. "
                "The outputs should preserve the tissue image background and expose meaningful enriched spatial neighborhoods."
                if not transfer_case
                else
                "Produce a PNG seqFISH spatial scatter plot colored by `celltype_mapped_refined` and a second PNG "
                "neighborhood-enrichment heatmap summarizing enriched cell-type interactions. "
                "The outputs should expose the expected mesoderm / endothelial neighborhood structure from the official seqFISH example."
                )
            ),
        )
    )
    candidate_repos = list(
        settings.get(
            "candidate_repos",
            [
                {"name": "squidpy", "url": "https://github.com/scverse/squidpy.git"},
                {"name": "scanpy", "url": "https://github.com/scverse/scanpy.git"},
            ],
        )
    )

    repo_records: list[dict[str, Any]] = []
    for repo in candidate_repos:
        repo_name = str(repo.get("name", "")).strip()
        repo_url = str(repo.get("url", "")).strip()
        if not repo_name or not repo_url:
            raise BenchmarkSetupError(f"Invalid candidate repo entry: {repo}")
        repo_path = repos_root / repo_name
        if (repo_path / ".git").exists():
            action = "updated"
            _run_command(["git", "-C", str(repo_path), "pull", "--ff-only"])
        else:
            action = "cloned"
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            _run_command(["git", "clone", "--depth", "1", repo_url, str(repo_path)])
        repo_records.append(
            {
                "name": repo_name,
                "url": repo_url,
                "path": str(repo_path),
                "action": action,
            }
        )

    methods_path.write_text(methods_excerpt + "\n", encoding="utf-8")
    target_desc_path.write_text(target_description + "\n", encoding="utf-8")
    _write_json(candidate_repo_path, repo_records)

    raw_data_filename = "seqfish.h5ad" if transfer_case else "visium_hne_adata_crop.h5ad"
    raw_data_path = data_dir / raw_data_filename
    reference_figure_path = reference_dir / "reference_spatial.png"
    reference_interaction_figure_path = reference_dir / "reference_nhood_enrichment.png"
    reference_summary_path = reference_dir / "reference_summary.json"
    generated_figure_path = generated_dir / "generated_spatial.png"
    generated_interaction_figure_path = generated_dir / "generated_nhood_enrichment.png"
    generated_summary_path = generated_dir / "generated_summary.json"

    task_spec = {
        "case_id": case_id,
        "title": str(
            settings.get(
                "title",
                f"Squidpy {dataset_label} method transfer"
                if transfer_case
                else (
                    "Squidpy Visium H&E interaction analysis"
                    if interaction_case
                    else "Squidpy Visium H&E spatial plot reproduction"
                ),
            )
        ),
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "reference_figure_path": str(reference_figure_path),
        "reference_interaction_figure_path": str(reference_interaction_figure_path) if interaction_case else "",
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
        "default_generated_figure_path": str(generated_figure_path),
        "default_generated_interaction_figure_path": str(generated_interaction_figure_path) if interaction_case else "",
        "default_generated_summary_path": str(generated_summary_path),
        "default_analysis_config": DEFAULT_ANALYSIS_CONFIG,
    }
    _write_json(task_spec_path, task_spec)

    manifest = {
        "case_id": case_id,
        "case_root": str(case_root),
        "repo_root": str(repos_root),
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "task_spec_path": str(task_spec_path),
        "raw_data_path": str(raw_data_path),
        "reference_figure_path": str(reference_figure_path),
        "reference_interaction_figure_path": str(reference_interaction_figure_path) if interaction_case else "",
        "reference_summary_path": str(reference_summary_path),
        "generated_figure_path": str(generated_figure_path),
        "generated_interaction_figure_path": str(generated_interaction_figure_path) if interaction_case else "",
        "generated_summary_path": str(generated_summary_path),
        "candidate_repos": repo_records,
    }
    _write_json(data_manifest_path, manifest)
    return manifest


def prepare_visium_multi_agent_case_study(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "case_study":
        raise BenchmarkSetupError("Benchmark config kind must be 'case_study'")
    case_id = str(settings.get("case_id", ""))
    if case_id not in {
        "visium_multi_agent_collaboration",
        "visium_multi_agent_monolith",
        "seqfish_multi_agent_collaboration",
        "seqfish_multi_agent_monolith",
    }:
        raise BenchmarkSetupError("Unsupported case-study id for this helper")
    monolith_case = case_id in {"visium_multi_agent_monolith", "seqfish_multi_agent_monolith"}
    seqfish_case = case_id in {"seqfish_multi_agent_collaboration", "seqfish_multi_agent_monolith"}
    dataset_id = "seqfish" if seqfish_case else "visium_hne_adata_crop"
    dataset_label = "seqFISH" if seqfish_case else "Visium H&E"

    from dynaforge.case_studies.scanpy_visium_cluster_job import DEFAULT_ANALYSIS_CONFIG as SCANPY_CLUSTER_DEFAULTS
    from dynaforge.case_studies.squidpy_visium_interactions_job import DEFAULT_ANALYSIS_CONFIG as SQUIDPY_INTERACTION_DEFAULTS

    if seqfish_case:
        default_case_root = (
            "benchmarks/case_studies/seqfish_multi_agent_monolith"
            if monolith_case
            else "benchmarks/case_studies/seqfish_multi_agent_collaboration"
        )
    else:
        default_case_root = (
            "benchmarks/case_studies/visium_multi_agent_monolith"
            if monolith_case
            else "benchmarks/case_studies/visium_multi_agent_collaboration"
        )
    case_root = Path(str(settings.get("case_root", default_case_root))).resolve()
    repos_root = Path(str(settings.get("repo_root", "external/case_studies/visium_multi_agent/repos"))).resolve()
    methods_path = case_root / "materials" / "methods_excerpt.txt"
    target_desc_path = case_root / "materials" / "target_figure_description.txt"
    candidate_repo_path = case_root / "candidate_repos.json"
    task_spec_path = case_root / "task_spec.json"
    data_manifest_path = case_root / "data_manifest.json"
    reference_dir = case_root / "reference"
    generated_dir = case_root / "generated"
    data_dir = case_root / "data"
    for path in (reference_dir, generated_dir, data_dir, methods_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    methods_excerpt = str(
        settings.get(
            "methods_excerpt",
            (
                (
                    "Load the public cropped Visium H&E dataset. First run a Scanpy-style gene-expression workflow to normalize counts, "
                    "select highly variable genes, build a PCA/neighborhood graph, cluster spots, and summarize marker genes. Then feed the annotated data "
                    "into a Squidpy-style spatial workflow that constructs a spatial-neighbor graph and computes neighborhood enrichment across the learned clusters."
                )
                if not seqfish_case
                else
                (
                    "Load the public seqFISH dataset. First run a Scanpy-style gene-expression workflow to normalize counts, "
                    "select highly variable genes, build a PCA/neighborhood graph, cluster cells, and summarize marker genes. Then feed the annotated data "
                    "into a Squidpy-style spatial workflow that constructs a spatial-neighbor graph and computes neighborhood enrichment across the learned clusters."
                )
            ),
        )
    )
    target_description = str(
        settings.get(
            "target_figure_description",
            (
                (
                    "Produce a collaboration-style figure package consisting of a Scanpy cluster UMAP, a Scanpy marker heatmap, a Squidpy spatial cluster plot, "
                    "and a Squidpy neighborhood-enrichment heatmap, along with structured clustering and interaction summaries."
                )
                if not seqfish_case
                else
                (
                    "Produce a collaboration-style figure package consisting of a Scanpy cluster UMAP, a Scanpy marker heatmap, a Squidpy seqFISH spatial plot, "
                    "and a Squidpy neighborhood-enrichment heatmap, along with structured clustering and interaction summaries."
                )
            ),
        )
    )
    candidate_repos = list(
        settings.get(
            "candidate_repos",
            [
                {"name": "scanpy", "url": "https://github.com/scverse/scanpy.git"},
                {"name": "scanpy-tutorials", "url": "https://github.com/scverse/scanpy-tutorials.git"},
                {"name": "squidpy", "url": "https://github.com/scverse/squidpy.git"},
            ],
        )
    )

    repo_records: list[dict[str, Any]] = []
    for repo in candidate_repos:
        repo_name = str(repo.get("name", "")).strip()
        repo_url = str(repo.get("url", "")).strip()
        if not repo_name or not repo_url:
            raise BenchmarkSetupError(f"Invalid candidate repo entry: {repo}")
        repo_path = repos_root / repo_name
        if (repo_path / ".git").exists():
            action = "updated"
            _run_command(["git", "-C", str(repo_path), "pull", "--ff-only"])
        else:
            action = "cloned"
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            _run_command(["git", "clone", "--depth", "1", repo_url, str(repo_path)])
        repo_records.append(
            {
                "name": repo_name,
                "url": repo_url,
                "path": str(repo_path),
                "action": action,
            }
        )

    methods_path.write_text(methods_excerpt + "\n", encoding="utf-8")
    target_desc_path.write_text(target_description + "\n", encoding="utf-8")
    _write_json(candidate_repo_path, repo_records)

    raw_data_path = data_dir / ("seqfish_raw.h5ad" if seqfish_case else "visium_hne_raw.h5ad")
    reference_cluster_figure_path = reference_dir / "reference_cluster_umap.png"
    reference_marker_figure_path = reference_dir / "reference_marker_heatmap.png"
    reference_spatial_figure_path = reference_dir / "reference_spatial.png"
    reference_interaction_figure_path = reference_dir / "reference_nhood_enrichment.png"
    reference_cluster_summary_path = reference_dir / "reference_cluster_summary.json"
    reference_interaction_summary_path = reference_dir / "reference_interaction_summary.json"
    reference_summary_path = reference_dir / "reference_summary.json"

    generated_cluster_figure_path = generated_dir / "generated_cluster_umap.png"
    generated_marker_figure_path = generated_dir / "generated_marker_heatmap.png"
    generated_spatial_figure_path = generated_dir / "generated_spatial.png"
    generated_interaction_figure_path = generated_dir / "generated_nhood_enrichment.png"
    generated_cluster_summary_path = generated_dir / "generated_cluster_summary.json"
    generated_interaction_summary_path = generated_dir / "generated_interaction_summary.json"
    generated_summary_path = generated_dir / "generated_summary.json"
    generated_annotated_data_path = generated_dir / "generated_annotated_visium.h5ad"

    task_spec = {
        "case_id": case_id,
        "title": str(
            settings.get(
                "title",
                (
                    f"{dataset_label} multi-specialized-agent monolith baseline"
                    if monolith_case
                    else f"{dataset_label} multi-specialized-agent collaboration"
                ),
            )
        ),
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "raw_data_path": str(raw_data_path),
        "reference_cluster_figure_path": str(reference_cluster_figure_path),
        "reference_marker_figure_path": str(reference_marker_figure_path),
        "reference_spatial_figure_path": str(reference_spatial_figure_path),
        "reference_interaction_figure_path": str(reference_interaction_figure_path),
        "reference_cluster_summary_path": str(reference_cluster_summary_path),
        "reference_interaction_summary_path": str(reference_interaction_summary_path),
        "reference_summary_path": str(reference_summary_path),
        "default_generated_cluster_figure_path": str(generated_cluster_figure_path),
        "default_generated_marker_figure_path": str(generated_marker_figure_path),
        "default_generated_spatial_figure_path": str(generated_spatial_figure_path),
        "default_generated_interaction_figure_path": str(generated_interaction_figure_path),
        "default_generated_cluster_summary_path": str(generated_cluster_summary_path),
        "default_generated_interaction_summary_path": str(generated_interaction_summary_path),
        "default_generated_summary_path": str(generated_summary_path),
        "default_generated_annotated_data_path": str(generated_annotated_data_path),
        "default_cluster_analysis_config": SCANPY_CLUSTER_DEFAULTS,
        "default_interaction_analysis_config": SQUIDPY_INTERACTION_DEFAULTS,
    }
    _write_json(task_spec_path, task_spec)

    manifest = {
        "case_id": case_id,
        "case_root": str(case_root),
        "repo_root": str(repos_root),
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_desc_path),
        "candidate_repos_path": str(candidate_repo_path),
        "task_spec_path": str(task_spec_path),
        "raw_data_path": str(raw_data_path),
        "reference_cluster_figure_path": str(reference_cluster_figure_path),
        "reference_marker_figure_path": str(reference_marker_figure_path),
        "reference_spatial_figure_path": str(reference_spatial_figure_path),
        "reference_interaction_figure_path": str(reference_interaction_figure_path),
        "reference_cluster_summary_path": str(reference_cluster_summary_path),
        "reference_interaction_summary_path": str(reference_interaction_summary_path),
        "reference_summary_path": str(reference_summary_path),
        "generated_cluster_figure_path": str(generated_cluster_figure_path),
        "generated_marker_figure_path": str(generated_marker_figure_path),
        "generated_spatial_figure_path": str(generated_spatial_figure_path),
        "generated_interaction_figure_path": str(generated_interaction_figure_path),
        "generated_cluster_summary_path": str(generated_cluster_summary_path),
        "generated_interaction_summary_path": str(generated_interaction_summary_path),
        "generated_summary_path": str(generated_summary_path),
        "generated_annotated_data_path": str(generated_annotated_data_path),
        "candidate_repos": repo_records,
    }
    _write_json(data_manifest_path, manifest)
    return manifest


def prepare_case_study_assets(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    runner_mode = str(settings.get("runner_mode", "")).strip().lower()
    if runner_mode in {"compiled_generic", "generic_compiled"}:
        return prepare_generic_case_study_assets(config)
    case_id = str(settings.get("case_id", ""))
    family = legacy_case_family(case_id)
    if family == "scanpy_pbmc3k":
        return prepare_scanpy_pbmc3k_case_study(config)
    if family == "scanpy_trajectory":
        return prepare_scanpy_paul15_case_study(config)
    if family == "visium_multi_agent":
        return prepare_visium_multi_agent_case_study(config)
    if family == "squidpy":
        return prepare_squidpy_visium_case_study(config)
    raise BenchmarkSetupError(f"Unsupported case-study id: {case_id}")


def prepare_generic_case_study_assets(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "case_study":
        raise BenchmarkSetupError("Generic case-study setup requires benchmark.kind=case_study")

    case_id = str(settings.get("case_id", "generic_case_study")).strip() or "generic_case_study"
    runner_mode = str(settings.get("runner_mode", "compiled_generic")).strip().lower() or "compiled_generic"
    case_root = Path(str(settings.get("case_root", f"benchmarks/case_studies/{case_id}"))).resolve()
    repos_root = Path(str(settings.get("repo_root", f"external/case_studies/{case_id}/repos"))).resolve()
    materials_dir = case_root / "materials"
    generated_dir = case_root / "generated"
    data_dir = case_root / "data"
    reference_dir = case_root / "reference"
    for path in (case_root, repos_root, materials_dir, generated_dir, data_dir, reference_dir):
        path.mkdir(parents=True, exist_ok=True)

    methods_excerpt = _resolve_case_text(
        settings.get("methods_excerpt"),
        fallback_path=materials_dir / "methods_excerpt.txt",
        default="",
    )
    target_figure_description = _resolve_case_text(
        settings.get("target_figure_description"),
        fallback_path=materials_dir / "target_figure_description.txt",
        default="",
    )
    task_notes = _resolve_case_text(
        settings.get("task_notes"),
        fallback_path=materials_dir / "task_notes.txt",
        default="",
    )

    candidate_repos = settings.get("candidate_repos", [])
    if not isinstance(candidate_repos, Sequence) or isinstance(candidate_repos, (str, bytes)):
        raise BenchmarkSetupError("benchmark.candidate_repos must be a sequence of repo descriptors")
    repo_records = _prepare_candidate_repo_records(candidate_repos, repos_root)
    candidate_repo_path = case_root / "candidate_repos.json"
    _write_json(candidate_repo_path, repo_records)

    input_assets = dict(settings.get("input_assets", {})) if isinstance(settings.get("input_assets", {}), Mapping) else {}
    public_bundle = str(input_assets.get("public_data_bundle", "")).strip()
    if public_bundle:
        prepared_assets = _prepare_public_case_data_bundle(
            public_bundle,
            data_dir=data_dir,
            reference_dir=reference_dir,
        )
        bundle_key = f"{public_bundle}_assets"
        input_assets[bundle_key] = prepared_assets
        input_assets.setdefault("prepared_public_bundle", public_bundle)
    expected_outputs = settings.get("expected_outputs", [])
    output_contract = dict(settings.get("output_contract", {})) if isinstance(settings.get("output_contract", {}), Mapping) else {}
    hidden_assets = dict(settings.get("hidden_assets", {})) if isinstance(settings.get("hidden_assets", {}), Mapping) else {}
    task_spec = {
        "title": str(settings.get("title", case_id.replace("_", " ").title())),
        "methods_excerpt": methods_excerpt,
        "target_figure_description": target_figure_description,
        "task_notes": task_notes,
        "input_assets": input_assets,
        "expected_outputs": list(expected_outputs) if isinstance(expected_outputs, Sequence) and not isinstance(expected_outputs, (str, bytes)) else [],
        "output_contract": output_contract,
    }
    task_spec_path = case_root / "task_spec.json"
    _write_json(task_spec_path, task_spec)
    hidden_manifest_path = case_root / "hidden_manifest.json"
    _write_json(hidden_manifest_path, hidden_assets)

    data_manifest = {
        "case_id": case_id,
        "input_assets": input_assets,
        "generated_dir": str(generated_dir),
        "data_dir": str(data_dir),
        "reference_dir": str(reference_dir),
    }
    data_manifest_path = case_root / "data_manifest.json"
    _write_json(data_manifest_path, data_manifest)

    return {
        "case_id": case_id,
        "case_root": str(case_root),
        "repo_root": str(repos_root),
        "generated_dir": str(generated_dir),
        "data_dir": str(data_dir),
        "reference_dir": str(reference_dir),
        "methods_excerpt": methods_excerpt,
        "target_figure_description": target_figure_description,
        "task_notes": task_notes,
        "candidate_repos_path": str(candidate_repo_path),
        "candidate_repos": repo_records,
        "task_spec_path": str(task_spec_path),
        "data_manifest_path": str(data_manifest_path),
        "hidden_manifest_path": str(hidden_manifest_path),
        "hidden_assets": hidden_assets,
        "input_assets": input_assets,
        "expected_outputs": task_spec["expected_outputs"],
        "output_contract": output_contract,
        "runner_mode": runner_mode,
    }


def _prepare_public_case_data_bundle(
    bundle_name: str,
    *,
    data_dir: Path,
    reference_dir: Path,
) -> dict[str, Any]:
    from dynaforge.case_studies.public_data import (
        ensure_cell2location_mouse_brain_assets,
        ensure_tenx_breast_visium_assets,
    )

    normalized = bundle_name.strip().lower()
    if normalized == "cell2location_mouse_brain":
        bundle_root = reference_dir / normalized
        return ensure_cell2location_mouse_brain_assets(bundle_root)
    if normalized == "tenx_visium_breast_cancer":
        bundle_root = data_dir / normalized
        return ensure_tenx_breast_visium_assets(bundle_root)
    raise BenchmarkSetupError(f"Unsupported public_data_bundle: {bundle_name}")


def _choose_medqa_config(config_names: Sequence[str]) -> str:
    for name in config_names:
        if "bigbio_qa" in name:
            return name
    return str(config_names[0])


def _resolve_case_text(value: Any, *, fallback_path: Path, default: str = "") -> str:
    if isinstance(value, str) and value.strip():
        candidate = Path(value)
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
        else:
            text = value.strip()
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
        return text
    if fallback_path.exists():
        return fallback_path.read_text(encoding="utf-8").strip()
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_path.write_text(default + ("\n" if default else ""), encoding="utf-8")
    return default


def _prepare_candidate_repo_records(candidate_repos: Sequence[Any], repos_root: Path) -> list[dict[str, Any]]:
    repo_records: list[dict[str, Any]] = []
    repos_root.mkdir(parents=True, exist_ok=True)
    for index, raw_repo in enumerate(candidate_repos):
        if not isinstance(raw_repo, Mapping):
            raise BenchmarkSetupError(f"Candidate repo entry at position {index} must be a mapping")
        repo = dict(raw_repo)
        repo_name = str(repo.get("name", "")).strip() or f"repo_{index}"
        repo_url = str(repo.get("url", "")).strip()
        repo_path_value = str(repo.get("path", "")).strip()
        if not repo_url and not repo_path_value:
            raise BenchmarkSetupError(f"Candidate repo '{repo_name}' requires either url or path")
        if repo_path_value:
            repo_path = Path(repo_path_value).expanduser().resolve()
            action = "existing"
        else:
            repo_path = repos_root / repo_name
            action = "existing"
            if not repo_path.exists():
                _run_command(["git", "clone", "--depth", "1", repo_url, str(repo_path)])
                action = "cloned"
        record = {
            "name": repo_name,
            "url": repo_url,
            "path": str(repo_path),
            "action": action,
        }
        for key, value in repo.items():
            if key in record:
                continue
            record[str(key)] = value
        repo_records.append(record)
    return repo_records


def _ordered_unique(values: Sequence[str | None]) -> list[str | None]:
    seen: set[str | None] = set()
    ordered: list[str | None] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _import_datasets_module() -> Any:
    _ensure_huggingface_cache_is_writable()
    try:
        import datasets
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise BenchmarkSetupError(
            "Benchmark preparation requires the 'datasets' package. Install with `pip install -e '.[benchmarks]'`."
        ) from exc
    return datasets


def _ensure_huggingface_cache_is_writable() -> None:
    project_root = Path(__file__).resolve().parents[2]
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")).expanduser()
    target = hf_home if _path_is_writable(hf_home) else project_root / ".hf_cache"
    target.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(target)
    os.environ.setdefault("HF_DATASETS_CACHE", str(target / "datasets"))
    os.environ.setdefault("HF_MODULES_CACHE", str(target / "modules"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(target / "hub"))


def _path_is_writable(path: Path) -> bool:
    probe = path if path.exists() else path.parent
    try:
        probe.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(probe, os.W_OK)


def _run_command(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd is not None else None,
            text=True,
            capture_output=capture_output,
            check=True,
        )
    except FileNotFoundError as exc:
        raise BenchmarkSetupError(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr or exc.stdout or str(exc)
        raise BenchmarkSetupError(message) from exc


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_cached_medqa_manifest(
    *,
    metadata_path: Path,
    dataset_id: str,
    chosen_config: str,
    split_candidates: Sequence[str | None],
    seed: int,
) -> dict[str, Any] | None:
    if not metadata_path.exists():
        return None
    try:
        manifest = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if manifest.get("dataset_id") != dataset_id:
        return None
    if manifest.get("chosen_config") != chosen_config:
        return None
    if manifest.get("chosen_split") not in {item for item in split_candidates if item is not None}:
        return None
    if int(manifest.get("seed", -1)) != seed:
        return None

    records_path = Path(str(manifest.get("records_path", "")))
    smoke_indices_path = Path(str(manifest.get("smoke_indices_path", "")))
    full_indices_path = Path(str(manifest.get("full_indices_path", "")))
    if not records_path.exists() or not smoke_indices_path.exists() or not full_indices_path.exists():
        return None

    try:
        if _count_lines(records_path) != int(manifest.get("record_count", -1)):
            return None
        if len(json.loads(smoke_indices_path.read_text(encoding="utf-8"))) != int(manifest.get("smoke_count", -1)):
            return None
        if len(json.loads(full_indices_path.read_text(encoding="utf-8"))) != int(manifest.get("full_count", -1)):
            return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    return manifest


def _prepare_aflow_public_benchmark_archive(settings: Mapping[str, Any]) -> Path:
    cache_dir = Path(str(settings.get("aflow_cache_dir", "benchmarks/aflow_public"))).resolve()
    archive_path = Path(str(settings.get("aflow_archive_path", cache_dir / "aflow_data.tar.gz"))).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        return archive_path

    temp_path = archive_path.with_name(f".{archive_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with urllib.request.urlopen(str(settings.get("aflow_package_url", _AFLOW_DATASET_URL)), timeout=120) as response:
            with temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        os.replace(temp_path, archive_path)
    except Exception as exc:  # pragma: no cover - network-dependent
        if temp_path.exists():
            temp_path.unlink()
        raise BenchmarkSetupError(f"Failed to download AFlow public benchmark archive: {exc}") from exc
    return archive_path


def _read_aflow_jsonl_records(archive_path: Path, member_name: str) -> list[dict[str, Any]]:
    with tarfile.open(archive_path, "r:gz") as archive:
        member = archive.extractfile(member_name)
        if member is None:
            raise BenchmarkSetupError(f"Missing {member_name} in AFlow archive {archive_path}")
        return [json.loads(line) for line in member]


def _load_generic_cached_manifest(
    metadata_path: Path,
    *,
    required_fields: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not metadata_path.exists():
        return None
    try:
        manifest = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    for key, expected_value in required_fields.items():
        if manifest.get(key) != expected_value:
            return None

    required_paths = (
        "records_path",
        "smoke_indices_path",
        "validation_indices_path",
        "test_indices_path",
    )
    if any(not Path(str(manifest.get(path_key, ""))).exists() for path_key in required_paths):
        return None
    return manifest


def _load_cached_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    return dict(payload)


def _load_cached_split_manifest(
    metadata_path: Path,
    *,
    required_fields: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not metadata_path.exists():
        return None
    try:
        manifest = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    for key, expected_value in required_fields.items():
        if manifest.get(key) != expected_value:
            return None

    split_record_counts = manifest.get("split_record_counts", {})
    split_record_paths = manifest.get("split_record_paths", {})
    if not isinstance(split_record_counts, Mapping) or not isinstance(split_record_paths, Mapping):
        return None
    for split_name, expected_count in split_record_counts.items():
        records_path = Path(str(split_record_paths.get(split_name, "")))
        if not records_path.exists():
            return None
        if _count_lines(records_path) != int(expected_count):
            return None

    sample_indices_path = Path(str(manifest.get("train_sample_indices_path", "")))
    sample_records_path = Path(str(manifest.get("train_sample_records_path", "")))
    if not sample_indices_path.exists() or not sample_records_path.exists():
        return None
    try:
        if len(json.loads(sample_indices_path.read_text(encoding="utf-8"))) != int(manifest.get("train_sample_count", -1)):
            return None
        if _count_lines(sample_records_path) != int(manifest.get("train_sample_count", -1)):
            return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    return manifest


def _get_dataset_split_names(
    datasets_module: Any,
    dataset_id: str,
    config_name: str | None = None,
    *,
    trust_remote_code: bool = False,
) -> list[str]:
    getter = getattr(datasets_module, "get_dataset_split_names", None)
    if getter is not None:
        if config_name is None:
            return list(getter(dataset_id, trust_remote_code=trust_remote_code))
        return list(getter(dataset_id, config_name, trust_remote_code=trust_remote_code))

    if dataset_id == "bigbio/med_qa":
        return ["train", "test", "validation"]
    if dataset_id == "EleutherAI/hendrycks_math":
        return ["train", "test"]
    if dataset_id in {"openai/openai_humaneval", "openai_humaneval"}:
        return ["test"]
    raise BenchmarkSetupError(f"Unable to determine dataset split names for {dataset_id}")


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=True, indent=2))


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    _atomic_write_text(
        path,
        "\n".join(json.dumps(record, ensure_ascii=True) for record in records) + ("\n" if records else ""),
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


def _count_lines(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _sample_indices(total_size: int, desired_count: int | None, *, seed: int) -> list[int]:
    if total_size <= 0:
        return []
    if desired_count is None:
        count = total_size
    else:
        count = min(total_size, max(desired_count, 0))
    if count == total_size:
        return list(range(total_size))
    rng = random.Random(seed)
    return sorted(rng.sample(list(range(total_size)), count))


def _split_validation_test_indices(
    total_size: int,
    *,
    validation_fraction: float,
    seed: int,
) -> tuple[list[int], list[int]]:
    if total_size <= 0:
        return [], []
    rng = random.Random(seed)
    indices = list(range(total_size))
    rng.shuffle(indices)
    validation_count = int(round(total_size * validation_fraction))
    validation_count = max(1, min(total_size - 1, validation_count)) if total_size > 1 else total_size
    validation = sorted(indices[:validation_count])
    test = sorted(indices[validation_count:])
    return validation, test


def _coerce_desired_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in {"all", "full", "none"}:
            return None
        return int(stripped)
    parsed = int(value)
    if parsed <= 0:
        return None
    return parsed


def _extract_boxed_text(value: str) -> str:
    if not value:
        return ""
    matches: list[str] = []
    needle = r"\boxed{"
    start = 0
    while True:
        idx = value.find(needle, start)
        if idx < 0:
            break
        cursor = idx + len(needle)
        depth = 1
        content_start = cursor
        while cursor < len(value):
            char = value[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    matches.append(value[content_start:cursor].strip())
                    break
            cursor += 1
        start = idx + len(needle)
    if matches:
        return matches[-1]
    return ""


def _extract_math_answer_from_solution(solution: str) -> str:
    stripped = solution.strip()
    if not stripped:
        return ""
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    candidates = list(reversed(lines[-4:])) if lines else [stripped]
    for candidate in candidates:
        boxed = _extract_boxed_text(candidate)
        if boxed:
            return boxed
        normalized = candidate.strip().rstrip(".")
        if not normalized:
            continue
        if "answer is" in normalized.lower():
            tail = normalized.split("answer is", 1)[1].strip(" :")
            if tail:
                return tail.rstrip(".")
        if "=" in normalized and any(token in normalized for token in ("\\", "sqrt", "frac", "/", "%", "^")):
            return normalized.split("=")[-1].strip().rstrip(".")
        if normalized.count(" ") <= 4:
            return normalized
    return lines[-1].rstrip(".") if lines else stripped.rstrip(".")


def _normalize_math_expression(value: str) -> str:
    stripped = value.strip()
    boxed = _extract_boxed_text(stripped)
    if boxed:
        stripped = boxed
    stripped = stripped.replace("\\left", "").replace("\\right", "")
    stripped = stripped.replace("\\!", "")
    stripped = _replace_frac_commands(stripped)
    stripped = _replace_sqrt_commands(stripped)
    stripped = re.sub(r"\\text\{[^{}]*\}", "", stripped)
    stripped = stripped.replace("\\%", "%")
    stripped = stripped.replace("\\pi", "pi").replace("π", "pi")
    stripped = stripped.replace("\\cdot", "*").replace("\\times", "*")
    stripped = stripped.replace("^", "**")
    stripped = stripped.strip("$")
    stripped = stripped.rstrip(".")
    stripped = stripped.replace("{", "(").replace("}", ")")
    stripped = re.sub(r"(?<=\d)(?=pi|sqrt|\()", "*", stripped)
    stripped = re.sub(r"(?<=\))(?=\d|pi|sqrt|\()", "*", stripped)
    stripped = re.sub(r"(?<=pi)(?=\d|\()", "*", stripped)
    stripped = re.sub(r"\s+", "", stripped)
    return stripped


def _math_equal(prediction: Any, reference: Any) -> bool:
    predicted = _normalize_math_expression(str(prediction))
    expected = _normalize_math_expression(str(reference))
    if not predicted or not expected:
        return False
    if predicted == expected:
        return True
    if _normalize_text(predicted) == _normalize_text(expected):
        return True

    prediction_numbers = _parse_numeric_math_values(predicted)
    reference_numbers = _parse_numeric_math_values(expected)
    for prediction_number in prediction_numbers:
        for reference_number in reference_numbers:
            if abs(prediction_number - reference_number) <= 1e-3:
                return True

    try:
        from sympy import N, simplify
        from sympy.parsing.sympy_parser import (
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )

        transformations = standard_transformations + (implicit_multiplication_application,)
        parsed_prediction = parse_expr(predicted, transformations=transformations)
        parsed_reference = parse_expr(expected, transformations=transformations)
        if simplify(parsed_prediction - parsed_reference) == 0:
            return True
        return abs(float(N(parsed_prediction)) - float(N(parsed_reference))) <= 1e-3
    except Exception:
        return False


def _parse_numeric_math_values(value: str) -> list[float]:
    cleaned = value.replace(",", "")
    cleaned = cleaned.replace("\\$", "").replace("$", "")
    cleaned = re.sub(r"(degrees?|squareunits?|units?)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
        try:
            raw = float(cleaned)
            return [raw, raw / 100.0]
        except ValueError:
            return []
    try:
        return [float(cleaned)]
    except ValueError:
        return []


def _replace_sqrt_commands(value: str) -> str:
    needle = r"\sqrt{"
    while needle in value:
        idx = value.find(needle)
        replacement, end = _extract_braced_segment(value, idx + len(needle) - 1)
        if replacement is None or end is None:
            break
        value = value[:idx] + f"sqrt({replacement})" + value[end + 1 :]
    return value


def _replace_frac_commands(value: str) -> str:
    needle = r"\frac{"
    while needle in value:
        idx = value.find(needle)
        numerator, num_end = _extract_braced_segment(value, idx + len(needle) - 1)
        if numerator is None or num_end is None:
            break
        if num_end + 1 >= len(value) or value[num_end + 1] != "{":
            break
        denominator, den_end = _extract_braced_segment(value, num_end + 1)
        if denominator is None or den_end is None:
            break
        value = value[:idx] + f"(({numerator})/({denominator}))" + value[den_end + 1 :]
    return value


def _extract_braced_segment(value: str, brace_index: int) -> tuple[str | None, int | None]:
    if brace_index >= len(value) or value[brace_index] != "{":
        return None, None
    depth = 1
    cursor = brace_index + 1
    start = cursor
    while cursor < len(value):
        char = value[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start:cursor], cursor
        cursor += 1
    return None, None


def _extract_code_block(value: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", value, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _humaneval_support_prelude(entry_point: str) -> str:
    if entry_point == "decode_cyclic":
        return (
            "def encode_cyclic(s: str):\n"
            "    groups = [s[(3 * i):min((3 * i + 3), len(s))] for i in range((len(s) + 2) // 3)]\n"
            "    groups = [(group[1:] + group[0]) if len(group) == 3 else group for group in groups]\n"
            "    return ''.join(groups)\n\n"
        )
    if entry_point == "decode_shift":
        return (
            "def encode_shift(s: str):\n"
            "    return ''.join([chr(((ord(ch) + 5 - ord('a')) % 26) + ord('a')) for ch in s])\n\n"
        )
    if entry_point == "find_zero":
        return "def poly(xs: list, x: float):\n    return sum(coeff * (x ** i) for i, coeff in enumerate(xs))\n\n"
    return ""


def _execute_humaneval_check(
    *,
    completion: str,
    test: str,
    entry_point: str,
    python_executable: str,
    timeout_s: int,
) -> tuple[str, str]:
    support_code = _humaneval_support_prelude(entry_point)
    harness = (
        "import hashlib\n"
        "import math\n"
        "import re\n"
        "from typing import Any, Dict, List, Optional, Tuple\n\n"
        f"{support_code}"
        f"{completion.strip()}\n\n"
        f"{test.strip()}\n\n"
        f"check({entry_point})\n"
    )
    with tempfile.TemporaryDirectory(prefix="dynaforge_humaneval_") as tmp_dir:
        script_path = Path(tmp_dir) / "check.py"
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
            return "timeout", "execution timed out"
    if completed.returncode == 0:
        return "passed", "passed"
    detail = completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}"
    return "failed", detail


def _execute_humaneval_assertions(
    *,
    completion: str,
    assertions: Sequence[str],
    entry_point: str,
    python_executable: str,
    timeout_s: int,
) -> tuple[str, str]:
    support_code = _humaneval_support_prelude(entry_point)
    assertion_lines = [line.strip() for line in assertions if str(line).strip()]
    harness = (
        "import hashlib\n"
        "import math\n"
        "import re\n"
        "from typing import Any, Dict, List, Optional, Tuple\n\n"
        f"{support_code}"
        f"{completion.strip()}\n\n"
        f"candidate = {entry_point}\n"
        f"{chr(10).join(assertion_lines)}\n"
    )
    with tempfile.TemporaryDirectory(prefix="dynaforge_humaneval_public_") as tmp_dir:
        script_path = Path(tmp_dir) / "check.py"
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
            return "timeout", "public test execution timed out"
    if completed.returncode == 0:
        return "passed", "passed"
    detail = completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}"
    return "failed", detail


def _extract_medqa_answer_text(answer: Any) -> str:
    if isinstance(answer, list) and answer:
        return str(answer[0]).strip()
    if answer is None:
        return ""
    return str(answer).strip()


def _normalize_text(value: str) -> str:
    lowered = value.lower().strip()
    return re.sub(r"\s+", " ", lowered)


def _lookup_option_text(label: str, options: Sequence[Mapping[str, Any]]) -> str:
    for option in options:
        if str(option.get("label", "")).strip().upper() == label:
            return str(option.get("text", "")).strip()
    return ""


def _match_medqa_option_text(
    candidate_text: str,
    options: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    normalized_candidate = _normalize_text(candidate_text)
    if not normalized_candidate:
        return "", ""

    for option in options:
        option_text = str(option.get("text", "")).strip()
        option_label = str(option.get("label", "")).strip().upper()
        normalized_option = _normalize_text(option_text)
        if normalized_candidate == normalized_option:
            return option_label, option_text
        if normalized_option and normalized_option in normalized_candidate:
            return option_label, option_text

    candidate_tokens = _tokenize_text(candidate_text)
    best_label = ""
    best_text = ""
    best_score = 0.0
    for option in options:
        option_text = str(option.get("text", "")).strip()
        option_label = str(option.get("label", "")).strip().upper()
        option_tokens = _tokenize_text(option_text)
        if not option_tokens:
            continue
        overlap = len(candidate_tokens & option_tokens)
        score = overlap / float(len(option_tokens))
        if score > best_score:
            best_label = option_label
            best_text = option_text
            best_score = score

    if best_score >= 0.5:
        return best_label, best_text
    return "", ""


def _tokenize_text(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))
