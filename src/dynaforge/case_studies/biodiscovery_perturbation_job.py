from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from dynaforge.case_studies.job_utils import ensure_output_dir, load_job_request, read_secret, write_job_result


OBJECTIVE_ALIASES = {
    "Carnevale22_Adenosine": "Carnevale22_Adenosine",
    "IFNG": "IFNG",
    "IL2": "IL2",
    "Steinhart": "Steinhart_crispra_GD2_D22",
    "Steinhart_crispra_GD2_D22": "Steinhart_crispra_GD2_D22",
}


def _parse_analyze_stdout(stdout: str) -> dict[str, Any]:
    result: dict[str, Any] = {"raw_stdout": stdout}
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Model: "):
            result["summary_line"] = stripped
            break
    return result


def _normalize_objective(label: str) -> str:
    normalized = OBJECTIVE_ALIASES.get(str(label).strip(), "").strip()
    if not normalized:
        raise RuntimeError(f"Unsupported BioDiscoveryAgent objective: {label}")
    return normalized


def _write_runtime_shims(repo_dir: Path) -> list[str]:
    created: list[str] = []
    shim_files = {
        "anthropic.py": (
            'HUMAN_PROMPT = "\\n\\nHuman:"\n'
            'AI_PROMPT = "\\n\\nAssistant:"\n'
            "class APIStatusError(Exception):\n    pass\n"
            "class _Unavailable:\n"
            "    def __getattr__(self, name):\n"
            '        raise RuntimeError("anthropic shim is unavailable for this sandboxed BioDiscoveryAgent run")\n'
            "class Anthropic:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        self.messages = _Unavailable()\n"
            "        self.completions = _Unavailable()\n"
        ),
        "tiktoken.py": (
            "class _Encoding:\n"
            "    def encode(self, text):\n"
            "        return list(str(text).encode('utf-8'))\n"
            "def get_encoding(_name):\n"
            "    return _Encoding()\n"
        ),
        "arxiv.py": (
            "class Search:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        self.args = args\n"
            "        self.kwargs = kwargs\n"
            "class Client:\n"
            "    def results(self, _search):\n"
            "        return []\n"
            "class SortCriterion:\n"
            "    Relevance = 'relevance'\n"
            "class SortOrder:\n"
            "    Descending = 'descending'\n"
        ),
        "scholarly.py": (
            "def search_pubs(_query):\n"
            "    return iter([{'title': 'No scholarly access in sandbox shim'}])\n"
            "def pprint(_obj):\n"
            "    return None\n"
        ),
        "pymed.py": (
            "class PubMed:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        pass\n"
            "    def query(self, *args, **kwargs):\n"
            "        return []\n"
        ),
    }
    langchain_dir = repo_dir / "langchain"
    langchain_init = langchain_dir / "__init__.py"
    langchain_tools = langchain_dir / "tools.py"
    if not langchain_init.exists():
        langchain_dir.mkdir(parents=True, exist_ok=True)
        langchain_init.write_text("from .tools import BaseTool, tool\n", encoding="utf-8")
        created.append(str(langchain_init))
    if not langchain_tools.exists():
        langchain_dir.mkdir(parents=True, exist_ok=True)
        langchain_tools.write_text(
            "class BaseTool:\n"
            "    pass\n\n"
            "def tool(*args, **kwargs):\n"
            "    def decorator(fn):\n"
            "        return fn\n"
            "    if args and callable(args[0]) and not kwargs:\n"
            "        return args[0]\n"
            "    return decorator\n",
            encoding="utf-8",
        )
        created.append(str(langchain_tools))
    for relative_path, content in shim_files.items():
        target = repo_dir / relative_path
        if target.exists():
            continue
        target.write_text(content, encoding="utf-8")
        created.append(str(target))
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BioDiscoveryAgent perturbation-design job.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    _, inputs, _, hints = load_job_request(args.input_json)
    output_dir = ensure_output_dir(hints, args.output_json)
    specialist_a = dict(inputs.get("specialist_a", {})) if isinstance(inputs.get("specialist_a", {}), Mapping) else {}
    routed_objective = _normalize_objective(
        str(specialist_a.get("routed_objective", "Carnevale22_Adenosine")).strip() or "Carnevale22_Adenosine"
    )
    bio_cfg = dict(hints.get("input_assets", {}).get("biodiscovery", {})) if isinstance(hints.get("input_assets", {}).get("biodiscovery", {}), Mapping) else {}

    repo_root = Path(str(hints.get("repo_root", ""))).resolve()
    repo_dir = repo_root / "BioDiscoveryAgent"
    if not repo_dir.exists():
        repo_dir = repo_root / "BioDiscoveryAgent".lower()
    if not repo_dir.exists():
        repo_dir = Path(str(hints.get("input_assets", {}).get("biodiscovery_repo_path", ""))).resolve()
    if not repo_dir.exists():
        raise RuntimeError("BioDiscoveryAgent repo path could not be resolved from case hints")

    model_name = str(bio_cfg.get("model", "gpt-4o")).strip() or "gpt-4o"
    steps = int(bio_cfg.get("steps", 3))
    num_genes = int(bio_cfg.get("num_genes", 32))
    task_name = str(bio_cfg.get("task", "perturb-genes-brief")).strip() or "perturb-genes-brief"
    essential = int(bio_cfg.get("essential", 1))

    openai_key = read_secret("openai_api_key")
    if not openai_key:
        raise RuntimeError("BioDiscoveryAgent job requires .secrets/openai_api_key")
    (repo_dir / "openai_api_key.txt").write_text(f"none:{openai_key}\n", encoding="utf-8")
    shim_files = _write_runtime_shims(repo_dir)

    run_name = "test"
    log_dir = model_name
    cli_cmd = [
        sys.executable,
        "research_assistant.py",
        "--task",
        task_name,
        "--model",
        model_name,
        "--data_name",
        routed_objective,
        "--steps",
        str(steps),
        "--num_genes",
        str(num_genes),
        "--log_dir",
        log_dir,
        "--run_name",
        run_name,
        "--python",
        sys.executable,
    ]
    completed = subprocess.run(
        cli_cmd,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(os.environ),
            "PYTHONPATH": str(repo_dir)
            + (
                f"{os.pathsep}{os.environ['PYTHONPATH']}"
                if os.environ.get("PYTHONPATH")
                else ""
            ),
        },
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "BioDiscoveryAgent CLI failed")

    prediction_dir = repo_dir / f"{log_dir}_{routed_objective}" / run_name
    sampled_genes_path = prediction_dir / f"sampled_genes_{steps}.npy"
    if not sampled_genes_path.exists():
        raise RuntimeError(f"BioDiscoveryAgent did not produce {sampled_genes_path}")
    predicted_genes = [str(item) for item in np.load(sampled_genes_path, allow_pickle=True).tolist()]

    analyze_cmd = [
        sys.executable,
        "analyze.py",
        "--model",
        model_name,
        "--dataset",
        routed_objective,
        "--rounds",
        str(steps),
        "--trials",
        "1",
        "--essential",
        str(essential),
    ]
    analysis = subprocess.run(
        analyze_cmd,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(os.environ),
            "PYTHONPATH": str(repo_dir)
            + (
                f"{os.pathsep}{os.environ['PYTHONPATH']}"
                if os.environ.get("PYTHONPATH")
                else ""
            ),
        },
    )
    eval_payload = _parse_analyze_stdout(analysis.stdout)
    eval_payload["returncode"] = analysis.returncode
    eval_payload["stderr"] = analysis.stderr
    if analysis.returncode != 0:
        raise RuntimeError(analysis.stderr or analysis.stdout or "BioDiscoveryAgent analyze.py failed")

    ranked_path = output_dir / "ranked_gene_batch.tsv"
    pd.DataFrame({"rank": range(1, len(predicted_genes) + 1), "gene": predicted_genes}).to_csv(ranked_path, sep="\t", index=False)
    raw_predictions_path = output_dir / "biodiscovery_raw_predictions.json"
    raw_predictions_path.write_text(
        json.dumps(
            {
                "selected_repo": "BioDiscoveryAgent",
                "routed_objective": routed_objective,
                "model": model_name,
                "predicted_genes": predicted_genes,
                "cli_stdout": completed.stdout,
                "cli_stderr": completed.stderr,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    eval_path = output_dir / "hitrate_eval.json"
    eval_path.write_text(json.dumps(eval_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    outputs = {
        "selected_repo": "BioDiscoveryAgent",
        "selected_specialists": ["BioDiscoveryAgent"],
        "routed_objective": routed_objective,
        "ranked_gene_batch_path": str(ranked_path),
        "raw_predictions_path": str(raw_predictions_path),
        "hitrate_eval_path": str(eval_path),
        "predicted_genes": predicted_genes,
        "summary": "BioDiscoveryAgent perturbation design completed.",
    }
    artifacts = [
        {"path": str(ranked_path), "mime": "text/tab-separated-values"},
        {"path": str(raw_predictions_path), "mime": "application/json"},
        {"path": str(eval_path), "mime": "application/json"},
    ]
    trace = {
        "specialist": "BioDiscoveryAgent",
        "routed_objective": routed_objective,
        "model": model_name,
        "prediction_dir": str(prediction_dir),
        "shim_files": shim_files,
    }
    write_job_result(
        output_json=args.output_json,
        outputs=outputs,
        summary={
            "ranked_gene_batch_path": str(ranked_path),
            "raw_predictions_path": str(raw_predictions_path),
            "hitrate_eval_path": str(eval_path),
        },
        artifacts=artifacts,
        trace=trace,
        confidence=0.84 if analysis.returncode == 0 else 0.7,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
