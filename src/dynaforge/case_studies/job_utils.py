from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


JsonDict = dict[str, Any]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_job_request(input_json: str | Path) -> tuple[JsonDict, JsonDict, JsonDict, JsonDict]:
    payload = json.loads(Path(input_json).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RuntimeError("job input payload must be a JSON object")
    inputs = dict(payload.get("inputs", {})) if isinstance(payload.get("inputs", {}), Mapping) else {}
    task = dict(inputs.get("task", {})) if isinstance(inputs.get("task", {}), Mapping) else {}
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    return dict(payload), inputs, task, hints


def ensure_output_dir(hints: Mapping[str, Any], output_json: str | Path) -> Path:
    output_dir = Path(str(hints.get("output_dir", Path(output_json).resolve().parent))).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def read_secret(name: str) -> str:
    secret_path = project_root() / ".secrets" / name
    if not secret_path.exists():
        return ""
    return secret_path.read_text(encoding="utf-8").strip()


def write_job_result(
    *,
    output_json: str | Path,
    outputs: Mapping[str, Any],
    summary: Mapping[str, Any] | None = None,
    artifacts: list[Mapping[str, Any]] | None = None,
    trace: Mapping[str, Any] | None = None,
    confidence: float = 0.85,
    status: str = "ok",
) -> None:
    payload = {
        "status": status,
        "outputs": dict(outputs),
        "summary": dict(summary or {}),
        "artifacts": list(artifacts or []),
        "trace": dict(trace or {}),
        "confidence": float(confidence),
    }
    Path(output_json).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
