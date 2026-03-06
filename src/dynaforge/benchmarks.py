from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from dynaforge.config import get_benchmark_settings


class BenchmarkSetupError(RuntimeError):
    """Raised when a benchmark asset or environment setup step fails."""


def prepare_medqa_assets(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "medqa":
        raise BenchmarkSetupError("Benchmark config kind must be 'medqa'")

    datasets_module = _import_datasets_module()
    dataset_id = str(settings.get("dataset_id", "bigbio/med_qa"))
    trust_remote_code = bool(settings.get("trust_remote_code", True))
    preview_path = Path(str(settings.get("preview_path", "benchmarks/medqa/medqa_preview.json")))
    info_path = Path(str(settings.get("info_path", "benchmarks/medqa/medqa_dataset_info.json")))
    sample_count = int(settings.get("sample_count", 1))
    requested_config = settings.get("config_name")
    requested_split = settings.get("split")

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.parent.mkdir(parents=True, exist_ok=True)

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


def setup_spatialbench_workspace(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "spatialbench":
        raise BenchmarkSetupError("Benchmark config kind must be 'spatialbench'")

    repo_url = str(settings.get("repo_url", "https://github.com/latchbio/spatialbench"))
    repo_path = Path(str(settings.get("repo_path", "external/spatialbench"))).resolve()
    venv_path = Path(str(settings.get("venv_path", repo_path / ".venv"))).resolve()
    validation_eval = str(
        settings.get(
            "validation_eval",
            "evals_canonical/qc/xenium_xenium_qc_filter_min_umi_counts.json",
        )
    )
    summary_path = Path(
        str(settings.get("validation_output_path", "benchmarks/spatialbench/validation_summary.json"))
    ).resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    if (repo_path / ".git").exists():
        repo_action = "updated"
        _run_command(["git", "-C", str(repo_path), "pull", "--ff-only"])
    else:
        repo_action = "cloned"
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        _run_command(["git", "clone", repo_url, str(repo_path)])

    python_exec, cli_exec = _venv_binaries(venv_path)
    if not python_exec.exists():
        _run_command([sys.executable, "-m", "venv", str(venv_path)])

    _run_command([str(python_exec), "-m", "pip", "install", "-e", "."], cwd=repo_path)
    validate_result = _run_command(
        [str(cli_exec), "validate", validation_eval],
        cwd=repo_path,
        capture_output=True,
    )

    summary = {
        "repo_url": repo_url,
        "repo_path": str(repo_path),
        "repo_action": repo_action,
        "venv_path": str(venv_path),
        "validation_eval": validation_eval,
        "validation_returncode": validate_result.returncode,
        "validation_stdout": validate_result.stdout,
        "validation_stderr": validate_result.stderr,
    }
    _write_json(summary_path, summary)
    return summary


def _choose_medqa_config(config_names: Sequence[str]) -> str:
    for name in config_names:
        if "bigbio_qa" in name:
            return name
    return str(config_names[0])


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
    try:
        import datasets
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise BenchmarkSetupError(
            "MedQA preparation requires the 'datasets' package. Install with `pip install -e '.[benchmarks]'`."
        ) from exc
    return datasets


def _venv_binaries(venv_path: Path) -> tuple[Path, Path]:
    if os.name == "nt":
        scripts_dir = venv_path / "Scripts"
        return scripts_dir / "python.exe", scripts_dir / "spatialbench.exe"
    bin_dir = venv_path / "bin"
    return bin_dir / "python", bin_dir / "spatialbench"


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
