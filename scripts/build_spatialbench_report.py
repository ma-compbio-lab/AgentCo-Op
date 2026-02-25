from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"missing": True, "path": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"missing": False, "path": str(path), "parse_error": str(exc)}


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _delta(a: Any, b: Any) -> float | None:
    av = _num(a)
    bv = _num(b)
    if av is None or bv is None:
        return None
    return bv - av


def build_report(run_dir: Path) -> tuple[dict[str, Any], str]:
    baseline_summary_path = run_dir / "baseline_summary.json"
    adaptive_summary_path = run_dir / "adaptive_summary.json"
    baseline = _load_summary(baseline_summary_path)
    adaptive = _load_summary(adaptive_summary_path)

    report = {
        "run_dir": str(run_dir),
        "baseline_summary_path": str(baseline_summary_path),
        "adaptive_summary_path": str(adaptive_summary_path),
        "baseline": baseline,
        "adaptive": adaptive,
        "comparison": {
            "delta_pass_rate": _delta(baseline.get("pass_rate"), adaptive.get("pass_rate")),
            "delta_invalid_rate": _delta(baseline.get("invalid_rate"), adaptive.get("invalid_rate")),
            "delta_avg_duration_s": _delta(baseline.get("avg_duration_s"), adaptive.get("avg_duration_s")),
        },
    }

    lines = [
        "# SpatialBench Overall Report",
        "",
        f"- Run directory: `{run_dir}`",
        f"- Baseline summary: `{baseline_summary_path}`",
        f"- Adaptive summary: `{adaptive_summary_path}`",
        "",
        "## Baseline",
        f"- total: {baseline.get('total')}",
        f"- passed: {baseline.get('passed')}",
        f"- pass_rate: {baseline.get('pass_rate')}",
        f"- invalid_rate: {baseline.get('invalid_rate')}",
        f"- avg_duration_s: {baseline.get('avg_duration_s')}",
        "",
        "## Adaptive",
        f"- total: {adaptive.get('total')}",
        f"- passed: {adaptive.get('passed')}",
        f"- pass_rate: {adaptive.get('pass_rate')}",
        f"- invalid_rate: {adaptive.get('invalid_rate')}",
        f"- avg_duration_s: {adaptive.get('avg_duration_s')}",
        "",
        "## Delta (adaptive - baseline)",
        f"- pass_rate: {report['comparison']['delta_pass_rate']}",
        f"- invalid_rate: {report['comparison']['delta_invalid_rate']}",
        f"- avg_duration_s: {report['comparison']['delta_avg_duration_s']}",
        "",
    ]

    return report, "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build aggregated SpatialBench report from one run directory.")
    parser.add_argument("--run-dir", required=True, help="Directory containing baseline/adaptive summaries.")
    parser.add_argument("--output-json", default=None, help="Output report JSON path.")
    parser.add_argument("--output-md", default=None, help="Output report markdown path.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output_json = Path(args.output_json) if args.output_json else (run_dir / "overall_report.json")
    output_md = Path(args.output_md) if args.output_md else (run_dir / "overall_report.md")
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    report, md = build_report(run_dir)
    output_json.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    output_md.write_text(md, encoding="utf-8")
    print(f"Report written: {output_json}")
    print(f"Report written: {output_md}")


if __name__ == "__main__":
    main()
