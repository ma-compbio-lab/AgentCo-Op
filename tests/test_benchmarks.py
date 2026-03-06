from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dynaforge.benchmarks import prepare_medqa_assets, setup_spatialbench_workspace


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
        assert split == "train[:1]"
        assert trust_remote_code is True
        return [{"id": "sample-1", "question": "test"}]


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
