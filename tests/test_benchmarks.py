from __future__ import annotations

import json
from pathlib import Path

from dynaforge.benchmarks import (
    grade_humaneval_prediction,
    grade_math_prediction,
    grade_medqa_prediction,
    normalize_humaneval_record,
    normalize_math_record,
    normalize_medqa_record,
    parse_medqa_answer,
    parse_math_answer,
    prepare_humaneval_dataset,
    prepare_math_dataset,
    prepare_medqa_assets,
    prepare_medqa_dataset,
    run_humaneval_public_tests,
)


class _FakeDatasetsModule:
    __version__ = "3.6.0"

    @staticmethod
    def get_dataset_config_names(dataset_id, trust_remote_code=False):
        if dataset_id == "bigbio/med_qa":
            assert trust_remote_code is True
            return ["med_qa_en_source", "med_qa_en_bigbio_qa"]
        if dataset_id == "EleutherAI/hendrycks_math":
            return ["counting_and_probability", "number_theory", "prealgebra", "precalculus"]
        raise AssertionError(f"Unexpected dataset_id: {dataset_id}")

    @staticmethod
    def get_dataset_split_names(dataset_id, config_name=None, trust_remote_code=False):
        if dataset_id == "bigbio/med_qa":
            assert trust_remote_code is True
            return ["train", "test", "validation"]
        if dataset_id == "EleutherAI/hendrycks_math":
            return ["train", "test"]
        if dataset_id in {"openai/openai_humaneval", "openai_humaneval"}:
            return ["test"]
        raise AssertionError(f"Unexpected dataset_id: {dataset_id}")

    @staticmethod
    def load_dataset(dataset_id, config_name=None, split=None, trust_remote_code=False):
        if dataset_id == "bigbio/med_qa":
            assert config_name == "med_qa_en_bigbio_qa"
            assert trust_remote_code is True
            if split == "train[:1]":
                return [{"id": "sample-1", "question": "test"}]
            if split == "train":
                return [
                    {
                        "id": "sample-1",
                        "question_id": "q1",
                        "document_id": "d1",
                        "question": "What is the best answer?",
                        "choices": ["Alpha", "Beta", "Gamma"],
                        "answer": ["Beta"],
                        "type": "multiple_choice",
                        "context": "",
                    },
                    {
                        "id": "sample-2",
                        "question_id": "q2",
                        "document_id": "d2",
                        "question": "Choose the final option.",
                        "choices": ["One", "Two", "Three"],
                        "answer": ["Three"],
                        "type": "multiple_choice",
                        "context": "",
                    },
                    {
                        "id": "sample-3",
                        "question_id": "q3",
                        "document_id": "d3",
                        "question": "Pick the first answer.",
                        "choices": ["Red", "Blue", "Green"],
                        "answer": ["Red"],
                        "type": "multiple_choice",
                        "context": "",
                    },
                ]
            return [
                {
                    "id": "sample-1",
                    "question_id": "q1",
                    "document_id": "d1",
                    "question": "What is the best answer?",
                    "choices": ["Alpha", "Beta", "Gamma"],
                    "answer": ["Beta"],
                    "type": "multiple_choice",
                    "context": "",
                },
                {
                    "id": "sample-2",
                    "question_id": "q2",
                    "document_id": "d2",
                    "question": "Choose the final option.",
                    "choices": ["One", "Two", "Three"],
                    "answer": ["Three"],
                    "type": "multiple_choice",
                    "context": "",
                },
            ]
        if dataset_id == "EleutherAI/hendrycks_math":
            assert split in {"train", "test"}
            records = {
                "counting_and_probability": [
                    {
                        "problem": "What is 1+1?",
                        "solution": "Therefore the answer is \\boxed{2}.",
                        "level": "Level 5",
                        "type": "Counting & Probability",
                    }
                ],
                "number_theory": [
                    {
                        "problem": "Compute 3+4.",
                        "solution": "Hence \\boxed{7}.",
                        "level": "Level 5",
                        "type": "Number Theory",
                    }
                ],
                "prealgebra": [
                    {
                        "problem": "Compute 5+6.",
                        "solution": "Result: \\boxed{11}.",
                        "level": "Level 5",
                        "type": "Prealgebra",
                    }
                ],
                "precalculus": [
                    {
                        "problem": "Compute 8+1.",
                        "solution": "We get \\boxed{9}.",
                        "level": "Level 5",
                        "type": "Precalculus",
                    }
                ],
            }
            if split == "train":
                return records[config_name] + [
                    {
                        "problem": f"Train problem for {config_name}.",
                        "solution": "Therefore the answer is \\boxed{10}.",
                        "level": "Level 3",
                        "type": records[config_name][0]["type"],
                    }
                ]
            return records[config_name]
        if dataset_id in {"openai/openai_humaneval", "openai_humaneval"}:
            assert split == "test"
            return [
                {
                    "task_id": "HumanEval/0",
                    "prompt": "def add(a, b):\n    \"\"\"Return the sum of two integers.\"\"\"\n",
                    "entry_point": "add",
                    "canonical_solution": "    return a + b\n",
                    "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n    assert candidate(-1, 4) == 3\n",
                },
                {
                    "task_id": "HumanEval/1",
                    "prompt": "def square(x):\n    \"\"\"Return x squared.\"\"\"\n",
                    "entry_point": "square",
                    "canonical_solution": "    return x * x\n",
                    "test": "def check(candidate):\n    assert candidate(3) == 9\n    assert candidate(-2) == 4\n",
                },
            ]
        raise AssertionError(f"Unexpected dataset_id: {dataset_id}")


def test_prepare_medqa_assets_writes_preview_and_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())
    preview_path = tmp_path / "medqa_preview.json"
    info_path = tmp_path / "medqa_dataset_info.json"

    metadata = prepare_medqa_assets(
        {
            "benchmark": {
                "kind": "medqa",
                "dataset_id": "bigbio/med_qa",
                "trust_remote_code": True,
                "split": "train",
                "sample_count": 1,
                "preview_path": str(preview_path),
                "info_path": str(info_path),
            }
        }
    )

    assert metadata["chosen_config"] == "med_qa_en_bigbio_qa"
    assert preview_path.exists()
    assert info_path.exists()


def test_prepare_math_dataset_writes_processed_records(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())

    manifest = prepare_math_dataset(
        {
            "benchmark": {
                "kind": "math",
                "data_source": "huggingface",
                "dataset_id": "EleutherAI/hendrycks_math",
                "processed_dir": str(tmp_path / "processed"),
                "split_seed": 42,
                "validation_fraction": 0.2,
                "smoke_count": 1,
            }
        }
    )

    records = Path(manifest["records_path"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(records) == 4
    first_record = json.loads(records[0])
    assert first_record["gold_answer"]
    assert manifest["metric"] == "solve_rate"
    assert Path(manifest["validation_indices_path"]).exists()
    assert Path(manifest["test_indices_path"]).exists()
    assert Path(manifest["upstream_full_manifest_path"]).exists()


def test_prepare_humaneval_dataset_writes_processed_records(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())

    manifest = prepare_humaneval_dataset(
        {
            "benchmark": {
                "kind": "humaneval",
                "data_source": "huggingface",
                "dataset_id": "openai/openai_humaneval",
                "processed_dir": str(tmp_path / "processed"),
                "split_seed": 42,
                "validation_fraction": 0.5,
                "smoke_count": 1,
            }
        }
    )

    records = Path(manifest["records_path"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(records) == 2
    first_record = json.loads(records[0])
    assert first_record["entry_point"]
    assert manifest["metric"] == "pass@1"
    assert Path(manifest["validation_indices_path"]).exists()
    assert Path(manifest["test_indices_path"]).exists()
    assert Path(manifest["upstream_full_manifest_path"]).exists()


def test_prepare_math_dataset_supports_aflow_package(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "dynaforge.benchmarks._prepare_aflow_public_benchmark_archive",
        lambda settings: tmp_path / "aflow.tar.gz",
    )
    monkeypatch.setattr(
        "dynaforge.benchmarks._read_aflow_jsonl_records",
        lambda archive_path, member_name: (
            [{"problem": "v", "solution": "\\boxed{1}", "level": "Level 5", "type": "Prealgebra"}]
            if member_name == "math_validate.jsonl"
            else [{"problem": "t", "solution": "\\boxed{2}", "level": "Level 5", "type": "Number Theory"}]
        ),
    )

    manifest = prepare_math_dataset(
        {
            "benchmark": {
                "kind": "math",
                "data_source": "aflow_package",
                "processed_dir": str(tmp_path / "processed"),
                "split_seed": 42,
                "validation_fraction": 0.2,
                "smoke_count": 1,
            }
        }
    )

    assert manifest["data_source"] == "aflow_package"
    assert manifest["validation_count"] == 1
    assert manifest["test_count"] == 1


def test_prepare_humaneval_dataset_supports_aflow_package(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "dynaforge.benchmarks._prepare_aflow_public_benchmark_archive",
        lambda settings: tmp_path / "aflow.tar.gz",
    )
    records_by_member = {
        "humaneval_validate.jsonl": [
            {
                "task_id": "HumanEval/0",
                "prompt": "def add(a, b):\n",
                "entry_point": "add",
                "canonical_solution": "    return a + b\n",
                "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
            }
        ],
        "humaneval_test.jsonl": [
            {
                "task_id": "HumanEval/1",
                "prompt": "def square(x):\n",
                "entry_point": "square",
                "canonical_solution": "    return x * x\n",
                "test": "def check(candidate):\n    assert candidate(3) == 9\n",
            }
        ],
        "humaneval_public_test.jsonl": [
            {
                "problem_id": "HumanEval/0",
                "entry_point": "add",
                "test": ["assert candidate(2, 5) == 7"],
            },
            {
                "problem_id": "HumanEval/1",
                "entry_point": "square",
                "test": ["assert candidate(4) == 16"],
            },
        ],
    }
    monkeypatch.setattr(
        "dynaforge.benchmarks._read_aflow_jsonl_records",
        lambda archive_path, member_name: records_by_member[member_name],
    )

    manifest = prepare_humaneval_dataset(
        {
            "benchmark": {
                "kind": "humaneval",
                "data_source": "aflow_package",
                "processed_dir": str(tmp_path / "processed"),
                "split_seed": 42,
                "validation_fraction": 0.2,
                "smoke_count": 1,
            }
        }
    )

    assert manifest["data_source"] == "aflow_package"
    assert manifest["validation_count"] == 1
    assert manifest["test_count"] == 1
    first_record = json.loads(Path(manifest["records_path"]).read_text(encoding="utf-8").splitlines()[0])
    assert first_record["public_tests"] == ["assert candidate(2, 5) == 7"]


def test_run_humaneval_public_tests_uses_assertion_list() -> None:
    sample = {
        "entry_point": "add",
        "public_tests": [
            "assert candidate(2, 5) == 7",
            "assert candidate(-1, 1) == 0",
        ],
    }

    passed = run_humaneval_public_tests({"completion": "def add(a, b):\n    return a + b\n"}, sample)
    failed = run_humaneval_public_tests({"completion": "def add(a, b):\n    return a - b\n"}, sample)

    assert passed["public_passed"] is True
    assert passed["public_test_count"] == 2
    assert failed["public_passed"] is False


def test_run_humaneval_public_tests_falls_back_to_visible_test_string() -> None:
    sample = {
        "entry_point": "square",
        "test": "def check(candidate):\n    assert candidate(3) == 9\n    assert candidate(-2) == 4\n",
    }

    passed = run_humaneval_public_tests({"completion": "def square(x):\n    return x * x\n"}, sample)
    failed = run_humaneval_public_tests({"completion": "def square(x):\n    return x + x\n"}, sample)

    assert passed["public_passed"] is True
    assert passed["public_test_count"] == 2
    assert failed["public_passed"] is False


def test_prepare_medqa_dataset_writes_processed_records(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())

    manifest = prepare_medqa_dataset(
        {
            "benchmark": {
                "kind": "medqa",
                "dataset_id": "bigbio/med_qa",
                "trust_remote_code": True,
                "config_name": "med_qa_en_bigbio_qa",
                "split": "train",
                "processed_dir": str(tmp_path / "processed"),
                "smoke_count": 1,
                "full_count": 2,
                "seed": 7,
            }
        }
    )

    records_path = Path(manifest["records_path"])
    lines = records_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    record = json.loads(lines[0])
    assert record["gold_answer_label"] == "B"
    assert record["options"][1]["text"] == "Beta"
    assert Path(manifest["smoke_indices_path"]).exists()
    assert Path(manifest["full_indices_path"]).exists()
    assert Path(manifest["all_splits_manifest_path"]).exists()


def test_prepare_medqa_dataset_supports_full_count_all(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())

    manifest = prepare_medqa_dataset(
        {
            "benchmark": {
                "kind": "medqa",
                "dataset_id": "bigbio/med_qa",
                "trust_remote_code": True,
                "config_name": "med_qa_en_bigbio_qa",
                "split": "test",
                "processed_dir": str(tmp_path / "processed"),
                "smoke_count": 1,
                "full_count": "all",
                "seed": 7,
            }
        }
    )

    full_indices = json.loads(Path(manifest["full_indices_path"]).read_text(encoding="utf-8"))
    smoke_indices = json.loads(Path(manifest["smoke_indices_path"]).read_text(encoding="utf-8"))
    assert manifest["chosen_split"] == "test"
    assert manifest["record_count"] == 2
    assert len(full_indices) == 2
    assert len(smoke_indices) == 1


def test_prepare_medqa_dataset_reuses_compatible_cached_manifest(monkeypatch, tmp_path) -> None:
    call_counter = {"load_dataset": 0}

    class _CountingDatasetsModule(_FakeDatasetsModule):
        @staticmethod
        def load_dataset(dataset_id, config_name, split, trust_remote_code=False):
            call_counter["load_dataset"] += 1
            return _FakeDatasetsModule.load_dataset(
                dataset_id,
                config_name,
                split,
                trust_remote_code=trust_remote_code,
            )

    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _CountingDatasetsModule())
    config = {
        "benchmark": {
            "kind": "medqa",
            "dataset_id": "bigbio/med_qa",
            "trust_remote_code": True,
            "config_name": "med_qa_en_bigbio_qa",
            "split": "test",
            "processed_dir": str(tmp_path / "processed"),
            "smoke_count": 1,
            "full_count": "all",
            "seed": 7,
        }
    }

    manifest_first = prepare_medqa_dataset(config)
    manifest_second = prepare_medqa_dataset(config)

    assert call_counter["load_dataset"] == 3
    assert manifest_first == manifest_second


def test_prepare_medqa_dataset_restores_all_splits_and_train_sample(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())

    manifest = prepare_medqa_dataset(
        {
            "benchmark": {
                "kind": "medqa",
                "dataset_id": "bigbio/med_qa",
                "trust_remote_code": True,
                "config_name": "med_qa_en_bigbio_qa",
                "split": "test",
                "processed_dir": str(tmp_path / "processed_test"),
                "all_splits_dir": str(tmp_path / "all_splits"),
                "smoke_count": 1,
                "full_count": "all",
                "train_sample_count": 2,
                "train_sample_seed": 13,
                "seed": 7,
            }
        }
    )

    all_splits_manifest = json.loads(Path(manifest["all_splits_manifest_path"]).read_text(encoding="utf-8"))
    assert all_splits_manifest["split_record_counts"]["train"] == 3
    assert all_splits_manifest["split_record_counts"]["test"] == 2
    assert all_splits_manifest["split_record_counts"]["validation"] == 2
    assert all_splits_manifest["train_sample_count"] == 2
    assert _count_jsonl(Path(all_splits_manifest["train_sample_records_path"])) == 2


def test_prepare_math_dataset_restores_upstream_train_and_test(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())
    monkeypatch.setattr(
        "dynaforge.benchmarks._prepare_aflow_public_benchmark_archive",
        lambda settings: tmp_path / "aflow.tar.gz",
    )
    monkeypatch.setattr(
        "dynaforge.benchmarks._read_aflow_jsonl_records",
        lambda archive_path, member_name: (
            [{"problem": "v", "solution": "\\boxed{1}", "level": "Level 5", "type": "Prealgebra"}]
            if member_name == "math_validate.jsonl"
            else [{"problem": "t", "solution": "\\boxed{2}", "level": "Level 5", "type": "Number Theory"}]
        ),
    )

    manifest = prepare_math_dataset(
        {
            "benchmark": {
                "kind": "math",
                "data_source": "aflow_package",
                "dataset_id": "EleutherAI/hendrycks_math",
                "processed_dir": str(tmp_path / "processed"),
                "upstream_dir": str(tmp_path / "upstream_full"),
                "split_seed": 42,
                "train_sample_count": 3,
                "validation_fraction": 0.2,
                "smoke_count": 1,
            }
        }
    )

    upstream_manifest = json.loads(Path(manifest["upstream_full_manifest_path"]).read_text(encoding="utf-8"))
    assert upstream_manifest["train_count"] == 8
    assert upstream_manifest["test_count"] == 4
    assert upstream_manifest["train_sample_count"] == 3
    assert _count_jsonl(Path(upstream_manifest["train_sample_records_path"])) == 3


def test_prepare_humaneval_dataset_handles_missing_train_split(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dynaforge.benchmarks._import_datasets_module", lambda: _FakeDatasetsModule())
    monkeypatch.setattr(
        "dynaforge.benchmarks._prepare_aflow_public_benchmark_archive",
        lambda settings: tmp_path / "aflow.tar.gz",
    )
    monkeypatch.setattr(
        "dynaforge.benchmarks._read_aflow_jsonl_records",
        lambda archive_path, member_name: (
            [{
                "task_id": "HumanEval/0",
                "prompt": "def add(a, b):\n",
                "entry_point": "add",
                "canonical_solution": "    return a + b\n",
                "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
            }]
            if member_name == "humaneval_validate.jsonl"
            else [{
                "task_id": "HumanEval/1",
                "prompt": "def square(x):\n",
                "entry_point": "square",
                "canonical_solution": "    return x * x\n",
                "test": "def check(candidate):\n    assert candidate(3) == 9\n",
            }]
        ),
    )

    manifest = prepare_humaneval_dataset(
        {
            "benchmark": {
                "kind": "humaneval",
                "data_source": "aflow_package",
                "processed_dir": str(tmp_path / "processed"),
                "upstream_dir": str(tmp_path / "upstream_full"),
                "split_seed": 42,
                "train_sample_count": 1000,
                "validation_fraction": 0.2,
                "smoke_count": 1,
            }
        }
    )

    upstream_manifest = json.loads(Path(manifest["upstream_full_manifest_path"]).read_text(encoding="utf-8"))
    assert upstream_manifest["train_available"] is False
    assert upstream_manifest["train_count"] == 0
    assert upstream_manifest["test_count"] == 2
    assert upstream_manifest["train_sample_count"] == 0
    assert _count_jsonl(Path(upstream_manifest["train_sample_records_path"])) == 0


def test_parse_and_grade_medqa_prediction() -> None:
    sample = normalize_medqa_record(
        {
            "id": "s1",
            "question": "What is correct?",
            "choices": ["Alpha", "Beta", "Gamma"],
            "answer": ["Beta"],
        }
    )

    parsed_label = parse_medqa_answer({"final_answer_label": "B"}, sample["options"])
    parsed_text = parse_medqa_answer("The best answer is Beta.", sample["options"])
    graded = grade_medqa_prediction({"answer_text": "Beta"}, sample)
    graded_conflict = grade_medqa_prediction(
        {
            "final_answer_label": "E",
            "final_answer_text": "Ectopic vitamin D production by lymphoma",
        },
        normalize_medqa_record(
            {
                "id": "s2",
                "question": "Cause of hypercalcemia?",
                "choices": [
                    "Osteoblastic metastasis",
                    "Ectopic vitamin D production",
                    "Ectopic PTH-related protein production",
                ],
                "answer": ["Ectopic vitamin D production"],
            }
        ),
    )

    assert parsed_label["label"] == "B"
    assert parsed_text["label"] == "B"
    assert graded["correct"] is True
    assert graded["prediction_label"] == "B"
    assert graded_conflict["prediction_label"] == "B"


def test_parse_and_grade_math_prediction() -> None:
    sample = normalize_math_record(
        {
            "problem": "Compute 2+3.",
            "solution": "So the answer is \\boxed{5}.",
            "level": "Level 5",
            "type": "Prealgebra",
        },
        config_name="prealgebra",
        record_index=0,
    )

    assert parse_math_answer({"final_answer": "\\boxed{5}"}) == "5"
    graded = grade_math_prediction({"final_answer": "5"}, sample)
    assert graded["correct"] is True


def test_normalize_and_grade_humaneval_prediction() -> None:
    sample = normalize_humaneval_record(
        {
            "task_id": "HumanEval/0",
            "prompt": "def add(a, b):\n    \"\"\"Return the sum of two integers.\"\"\"\n",
            "entry_point": "add",
            "canonical_solution": "    return a + b\n",
            "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        },
        record_index=0,
    )

    graded = grade_humaneval_prediction(
        {"completion": "def add(a, b):\n    return a + b\n"},
        sample,
    )
    assert graded["correct"] is True


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)
