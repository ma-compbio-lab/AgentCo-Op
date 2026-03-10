from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dynaforge.benchmarks import (
    grade_medqa_prediction,
    normalize_medqa_record,
    parse_medqa_answer,
    prepare_medqa_assets,
    prepare_medqa_dataset,
    prepare_spatialbench_manifests,
    setup_spatialbench_workspace,
)


class _FakeDatasetsModule:
    __version__ = "3.6.0"

    @staticmethod
    def get_dataset_config_names(dataset_id, trust_remote_code=False):
        assert dataset_id == "bigbio/med_qa"
        assert trust_remote_code is True
        return ["med_qa_en_source", "med_qa_en_bigbio_qa"]

    @staticmethod
    def load_dataset(dataset_id, config_name, split, trust_remote_code=False):
        assert dataset_id == "bigbio/med_qa"
        assert config_name == "med_qa_en_bigbio_qa"
        assert trust_remote_code is True
        if split == "train[:1]":
            return [{"id": "sample-1", "question": "test"}]
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


def test_setup_spatialbench_workspace_runs_expected_commands(monkeypatch, tmp_path) -> None:
    repo_path = tmp_path / "external" / "spatialbench"
    (repo_path / ".git").mkdir(parents=True)
    venv_path = repo_path / ".venv"
    python_exec = venv_path / "bin" / "python"
    cli_exec = venv_path / "bin" / "spatialbench"
    python_exec.parent.mkdir(parents=True)
    python_exec.write_text("", encoding="utf-8")
    cli_exec.write_text("", encoding="utf-8")

    calls = []

    def fake_run_command(cmd, *, cwd=None, capture_output=False):
        calls.append({"cmd": list(cmd), "cwd": str(cwd) if cwd is not None else None, "capture_output": capture_output})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("dynaforge.benchmarks._run_command", fake_run_command)
    summary_path = tmp_path / "benchmarks" / "spatialbench" / "validation_summary.json"

    summary = setup_spatialbench_workspace(
        {
            "benchmark": {
                "kind": "spatialbench",
                "repo_url": "https://github.com/latchbio/spatialbench",
                "repo_path": str(repo_path),
                "venv_path": str(venv_path),
                "validation_eval": "evals_canonical/qc/xenium_xenium_qc_filter_min_umi_counts.json",
                "validation_output_path": str(summary_path),
            }
        }
    )

    assert summary["repo_action"] == "updated"
    assert calls[0]["cmd"][:4] == ["git", "-C", str(repo_path), "pull"]
    assert calls[1]["cmd"][:4] == [str(python_exec), "-m", "pip", "install"]
    assert calls[2]["cmd"][0] == str(cli_exec)
    assert summary_path.exists()


def test_setup_spatialbench_workspace_resolves_relative_venv_paths(monkeypatch, tmp_path) -> None:
    repo_path = tmp_path / "external" / "spatialbench"
    (repo_path / ".git").mkdir(parents=True)
    venv_path = repo_path / ".venv"
    python_exec = venv_path / "bin" / "python"
    cli_exec = venv_path / "bin" / "spatialbench"
    python_exec.parent.mkdir(parents=True)
    python_exec.write_text("", encoding="utf-8")
    cli_exec.write_text("", encoding="utf-8")

    calls = []

    def fake_run_command(cmd, *, cwd=None, capture_output=False):
        calls.append({"cmd": list(cmd), "cwd": str(cwd) if cwd is not None else None})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("dynaforge.benchmarks._run_command", fake_run_command)
    monkeypatch.chdir(tmp_path)

    setup_spatialbench_workspace(
        {
            "benchmark": {
                "kind": "spatialbench",
                "repo_path": "external/spatialbench",
                "venv_path": "external/spatialbench/.venv",
                "validation_eval": "evals_canonical/qc/xenium_xenium_qc_filter_min_umi_counts.json",
                "validation_output_path": "benchmarks/spatialbench/validation_summary.json",
            }
        }
    )

    assert calls[1]["cmd"][0] == str(python_exec.resolve())
    assert calls[2]["cmd"][0] == str(cli_exec.resolve())


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
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["gold_answer_label"] == "B"
    assert record["options"][1]["text"] == "Beta"
    assert Path(manifest["smoke_indices_path"]).exists()
    assert Path(manifest["full_indices_path"]).exists()


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

    assert call_counter["load_dataset"] == 1
    assert manifest_first == manifest_second


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


def test_prepare_spatialbench_manifests_builds_records(tmp_path) -> None:
    repo_path = tmp_path / "external" / "spatialbench"
    eval_dir = repo_path / "evals_canonical" / "qc"
    eval_dir.mkdir(parents=True)
    eval_path = eval_dir / "xenium_xenium_qc_filter_min_umi_counts.json"
    eval_path.write_text(
        json.dumps(
            {
                "id": "xenium_qc_filter_min_umi_counts",
                "task": "Return JSON with cells_after_filtering.",
                "data_node": "latch://147398958.node",
                "grader": {"type": "numeric_tolerance", "config": {}},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = repo_path / "evals_canonical" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "id": "xenium_qc_filter_min_umi_counts",
                        "task_cat": "qc",
                        "platform": "xenium",
                        "grader": "numeric_tolerance",
                        "dest": "/tmp/xenium_xenium_qc_filter_min_umi_counts.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    prepared = prepare_spatialbench_manifests(
        {
            "benchmark": {
                "kind": "spatialbench",
                "repo_path": str(repo_path),
                "manifest_path": str(manifest_path),
                "processed_dir": str(tmp_path / "processed"),
                "smoke_count": 1,
                "full_count": 1,
            }
        }
    )

    records = Path(prepared["records_path"]).read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(records[0])
    assert record["platform"] == "xenium"
    assert record["grader_type"] == "numeric_tolerance"
    assert prepared["requires_latch_download"] is True
