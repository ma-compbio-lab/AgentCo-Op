from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generic JSON sandbox job used to validate compiled case-study execution.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    inputs = payload.get("inputs", {}) if isinstance(payload, dict) else {}
    task = inputs.get("task", {}) if isinstance(inputs, dict) else {}
    hints = task.get("hints", {}) if isinstance(task, dict) else {}
    candidate_repos = hints.get("candidate_repos", []) if isinstance(hints, dict) else []

    selected_repo = ""
    if isinstance(candidate_repos, list) and candidate_repos:
        first_repo = candidate_repos[0]
        if isinstance(first_repo, dict):
            selected_repo = str(first_repo.get("name", "")).strip()
    if not selected_repo:
        selected_repo = "unknown_repo"

    output_dir = Path(str(hints.get("output_dir", Path(args.output_json).resolve().parent)))
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "generic_repo_echo_report.json"
    report_payload = {
        "selected_repo": selected_repo,
        "expected_outputs": hints.get("expected_outputs", []),
        "input_assets": hints.get("input_assets", {}),
    }
    report_path.write_text(json.dumps(report_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    result_payload = {
        "status": "ok",
        "outputs": {
            "selected_repo": selected_repo,
            "summary": "Generic repo-transfer smoke path executed successfully.",
            "report_path": str(report_path),
        },
        "summary": {"report_path": str(report_path)},
        "artifacts": [{"path": str(report_path), "mime": "application/json"}],
        "confidence": 0.91,
        "trace": {"generic_repo_echo": True},
    }
    Path(args.output_json).write_text(json.dumps(result_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
