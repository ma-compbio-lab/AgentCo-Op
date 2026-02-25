from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPATIAL_ROOT = ROOT / "third_party" / "spatialbench"
if SPATIAL_ROOT.exists() and str(SPATIAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SPATIAL_ROOT))


def _load_eval_paths(eval_dir: str, max_samples: int | None) -> list[Path]:
    root = Path(eval_dir)
    if not root.exists():
        raise FileNotFoundError(f"Eval directory not found: {root}")
    paths = [p for p in root.rglob("*.json") if p.is_file() and p.name.lower() != "manifest.json"]
    paths = sorted(paths)
    if max_samples:
        paths = paths[: max_samples]
    return paths


def _collect_data_nodes(eval_paths: list[Path]) -> list[str]:
    from spatialbench.types import TestCase

    uris: set[str] = set()
    for path in eval_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        test_case = TestCase(**payload)
        node = test_case.data_node
        if isinstance(node, list):
            for item in node:
                if isinstance(item, str) and item:
                    uris.add(item)
        elif isinstance(node, str) and node:
            uris.add(node)
    return sorted(uris)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prefetch SpatialBench latch:// datasets for an eval directory.")
    parser.add_argument("--eval-dir", default=str(SPATIAL_ROOT / "evals_canonical"))
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument(
        "--quiet",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Suppress per-file progress from SpatialBench downloader.",
    )
    args = parser.parse_args()

    try:
        from spatialbench.harness.utils import batch_download_datasets
    except Exception as exc:
        raise RuntimeError(
            "SpatialBench is not importable. Install first: `pip install -e third_party/spatialbench`."
        ) from exc

    eval_paths = _load_eval_paths(args.eval_dir, args.max_samples)
    data_nodes = _collect_data_nodes(eval_paths)
    latch_nodes = [node for node in data_nodes if isinstance(node, str) and node.startswith("latch://")]

    print(
        f"SpatialBench prefetch start | evals={len(eval_paths)} | datasets={len(data_nodes)} | latch={len(latch_nodes)}"
    )
    if data_nodes:
        batch_download_datasets(data_nodes, show_progress=not args.quiet)
    print("SpatialBench prefetch complete")

    if args.summary:
        summary = {
            "eval_dir": str(Path(args.eval_dir)),
            "evals": len(eval_paths),
            "datasets": len(data_nodes),
            "latch_datasets": len(latch_nodes),
        }
        out = Path(args.summary)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
