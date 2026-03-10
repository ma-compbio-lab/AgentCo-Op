from __future__ import annotations

import json
from contextlib import contextmanager
import os
import random
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from dynaforge.config import get_benchmark_settings

try:  # pragma: no cover - platform-dependent
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


class BenchmarkSetupError(RuntimeError):
    """Raised when a benchmark asset or environment setup step fails."""


_OPTION_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


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


def prepare_medqa_dataset(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "medqa":
        raise BenchmarkSetupError("Benchmark config kind must be 'medqa'")

    datasets_module = _import_datasets_module()
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

    config_names = list(
        datasets_module.get_dataset_config_names(
            dataset_id,
            trust_remote_code=trust_remote_code,
        )
    )
    if not config_names:
        raise BenchmarkSetupError(f"No dataset configs found for {dataset_id}")

    chosen_config = str(requested_config) if requested_config in config_names else _choose_medqa_config(config_names)
    split_candidates = _ordered_unique([str(requested_split) if requested_split else None, "test", "validation", "train"])

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
        for key in ("final_answer_label", "answer_label", "choice", "label"):
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
        for key in ("final_answer_text", "answer_text", "answer", "result", "text"):
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


def prepare_spatialbench_manifests(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_benchmark_settings(config)
    if settings.get("kind") != "spatialbench":
        raise BenchmarkSetupError("Benchmark config kind must be 'spatialbench'")

    repo_path = Path(str(settings.get("repo_path", "external/spatialbench"))).resolve()
    manifest_path = Path(str(settings.get("manifest_path", repo_path / "evals_canonical" / "manifest.json"))).resolve()
    processed_dir = Path(str(settings.get("processed_dir", "benchmarks/spatialbench/processed"))).resolve()
    records_path = processed_dir / "records.jsonl"
    smoke_indices_path = processed_dir / "smoke_indices.json"
    full_indices_path = processed_dir / "full_indices.json"
    metadata_path = processed_dir / "dataset_manifest.json"
    smoke_count = int(settings.get("smoke_count", 2))
    full_count = int(settings.get("full_count", 10))
    seed = int(settings.get("seed", 0))

    if not manifest_path.exists():
        raise BenchmarkSetupError(f"SpatialBench manifest not found: {manifest_path}")

    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    examples = manifest_payload.get("examples", [])
    records: list[dict[str, Any]] = []
    for example in examples:
        dest = Path(str(example.get("dest", ""))).name
        eval_matches = list(repo_path.rglob(dest))
        if not eval_matches:
            raise BenchmarkSetupError(f"Unable to resolve eval file for manifest entry: {dest}")
        eval_path = eval_matches[0].resolve()
        eval_payload = json.loads(eval_path.read_text(encoding="utf-8"))
        records.append(
            {
                "id": str(example.get("id", eval_payload.get("id", eval_path.stem))),
                "task_category": str(example.get("task_cat", "")),
                "platform": str(example.get("platform", "")),
                "grader_type": str(example.get("grader", eval_payload.get("grader", {}).get("type", ""))),
                "eval_path": str(eval_path),
                "task": str(eval_payload.get("task", "")),
                "data_node": eval_payload.get("data_node"),
                "grader": eval_payload.get("grader", {}),
                "timeout": eval_payload.get("timeout"),
                "agent_timeout": eval_payload.get("agent_timeout"),
                "download_timeout": eval_payload.get("download_timeout"),
            }
        )

    processed_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(records_path, records)
    full_indices = _sample_indices(len(records), full_count, seed=seed)
    smoke_indices = full_indices[: min(smoke_count, len(full_indices))]
    _write_json(full_indices_path, full_indices)
    _write_json(smoke_indices_path, smoke_indices)

    manifest = {
        "record_count": len(records),
        "smoke_count": len(smoke_indices),
        "full_count": len(full_indices),
        "seed": seed,
        "records_path": str(records_path),
        "smoke_indices_path": str(smoke_indices_path),
        "full_indices_path": str(full_indices_path),
        "manifest_path": str(manifest_path),
        "requires_latch_download": any(record.get("data_node") is not None for record in records),
    }
    _write_json(metadata_path, manifest)
    return manifest


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
