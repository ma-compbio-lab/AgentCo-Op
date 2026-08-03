"""Tests for the Session-7.3 additions:
- `parallel_then_join` topology routing in `RepoCollaborationOrchestrator`
- the join_agent invocation contract
- the new `_compile_graph_parallel()` graph shape
- the Seurat / Signac / CellMarkerEvaluator adapters import + register

The CS2 wrappers are exercised end-to-end via the live run; here we
keep the offline-test surface tight: orchestrator routing, registry
side-effects, and the topology-graph shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentcoop.core.repo_collaboration import (
    CollaborationRequest,
    RepoCollaborationOrchestrator,
    register_local_adapter,
    get_local_adapter,
)


# ---------------------------------------------------------------------------
# Wrapper imports must register the per-agent adapters at import time.
# ---------------------------------------------------------------------------


def test_session_7_3_wrappers_register_adapters() -> None:
    import agentcoop.wrappers  # noqa: F401  (side-effect imports)

    for name in ("Seurat", "Signac", "CellMarkerEvaluator"):
        adapter = get_local_adapter(name)
        assert adapter is not None, f"missing local adapter for {name!r}"


# ---------------------------------------------------------------------------
# CollaborationRequest topology + join_agent fields are loadable from YAML.
# ---------------------------------------------------------------------------


def test_collaboration_request_yaml_with_topology(tmp_path: Path) -> None:
    yaml_text = """
case_id: ut_parallel
topology: parallel_then_join
repositories:
  - {name: AgentA, url: https://example.invalid/A, role_hint: a}
  - {name: AgentB, url: https://example.invalid/B, role_hint: b}
join_agent:
  name: JoinAgent
  role_hint: join + evaluate
  inputs:
    extra_file: /tmp/some.xlsx
parameters:
  top_n: 7
task:
  description: unit test
"""
    p = tmp_path / "req.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    req = CollaborationRequest.from_yaml(p)
    assert req.topology == "parallel_then_join"
    assert req.join_agent["name"] == "JoinAgent"
    assert req.join_agent["inputs"]["extra_file"] == "/tmp/some.xlsx"
    assert req.parameters["top_n"] == 7


# ---------------------------------------------------------------------------
# parallel_then_join orchestration runs N stub branches + a join_agent.
# ---------------------------------------------------------------------------


@register_local_adapter("UTBranchA")
def _ut_branch_a(req: dict[str, Any]) -> dict[str, Any]:
    out = Path(req["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    p = out / "rna_top_markers_by_celltype.json"
    p.write_text(json.dumps({"Basal": ["DES", "KRT14"]}), encoding="utf-8")
    return {
        "status": "success",
        "summary": "stub branch A",
        "warnings": [],
        "artifacts": {"rna_top_markers_json": str(p)},
    }


@register_local_adapter("UTBranchB")
def _ut_branch_b(req: dict[str, Any]) -> dict[str, Any]:
    out = Path(req["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    p = out / "atac_top_marker_genes_by_celltype.json"
    p.write_text(json.dumps({"Basal": ["DES", "TRP63"]}), encoding="utf-8")
    return {
        "status": "success",
        "summary": "stub branch B",
        "warnings": [],
        "artifacts": {"atac_top_marker_genes_json": str(p)},
    }


@register_local_adapter("UTJoiner")
def _ut_joiner(req: dict[str, Any]) -> dict[str, Any]:
    branches = req["input"]["branch_responses"]
    n_branches = len(branches)
    return {
        "status": "success",
        "summary": f"stub join over {n_branches} branches",
        "warnings": [],
        "artifacts": {"summary_csv": "/dev/null"},
        "main_results": {"n_branches": n_branches},
    }


def test_orchestrator_parallel_then_join_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = CollaborationRequest(
        case_id="ut_parallel_run",
        repositories=[
            {"name": "UTBranchA", "url": "https://example.invalid/A"},
            {"name": "UTBranchB", "url": "https://example.invalid/B"},
        ],
        topology="parallel_then_join",
        join_agent={"name": "UTJoiner", "role_hint": "join", "inputs": {}},
        task={"description": "ut"},
        dataset={"local_cache_dir": str(tmp_path / "data")},
    )
    workdir = tmp_path / "run"
    orch = RepoCollaborationOrchestrator(
        req, workdir=workdir, no_docker=True, external_root=tmp_path / "external",
    )
    res = orch.run()

    assert (workdir / "run_manifest.json").is_file()
    assert (workdir / "compiled_workflow_graph.json").is_file()

    manifest = json.loads((workdir / "run_manifest.json").read_text())
    assert manifest["topology"] == "parallel_then_join"
    assert "UTBranchA" in manifest["branch_responses"]
    assert "UTBranchB" in manifest["branch_responses"]
    assert manifest["branch_responses"]["UTBranchA"]["status"] == "success"
    assert manifest["branch_responses"]["UTBranchB"]["status"] == "success"
    # The join_agent's response gets persisted under its own artifacts dir.
    assert (workdir / "artifacts" / "utjoiner_run" / "response.json").is_file()
    # Topology graph node-set includes both branches and the join_agent.
    graph = json.loads((workdir / "compiled_workflow_graph.json").read_text())
    node_ids = {n["id"] for n in graph["nodes"]}
    assert "UTBranchA_run" in node_ids
    assert "UTBranchB_run" in node_ids
    assert "join_agent" in node_ids


# ---------------------------------------------------------------------------
# linear_handoff (CS1 default) keeps working — guard against accidental
# regressions to the existing path.
# ---------------------------------------------------------------------------


@register_local_adapter("UTLinearA")
def _ut_linear_a(req: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "summary": "stub upstream",
        "warnings": [],
        "artifacts": {"gene_set": ["DES", "MYH7"]},
    }


@register_local_adapter("UTLinearB")
def _ut_linear_b(req: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "summary": "stub downstream",
        "warnings": [],
        "artifacts": {},
    }


def test_orchestrator_linear_handoff_still_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = CollaborationRequest(
        case_id="ut_linear_run",
        repositories=[
            {"name": "UTLinearA", "url": "https://example.invalid/A"},
            {"name": "UTLinearB", "url": "https://example.invalid/B"},
        ],
        # No topology field -> default is linear_handoff.
        task={"description": "ut linear", "organism": "human"},
        dataset={"local_cache_dir": str(tmp_path / "data")},
    )
    workdir = tmp_path / "run_linear"
    orch = RepoCollaborationOrchestrator(
        req, workdir=workdir, no_docker=True, external_root=tmp_path / "external",
    )
    res = orch.run()

    manifest = json.loads((workdir / "run_manifest.json").read_text())
    assert manifest["topology"] == "linear_handoff"
    assert manifest["branch_responses"] == {}
    # Linear handoff still emits the gene-set broker file.
    assert (workdir / "artifacts" / "utlinearb_input.json").is_file()


# ---------------------------------------------------------------------------
# CellMarker evaluator surface — exercise the gold-set + P/R helpers
# without hitting the real Excel file.
# ---------------------------------------------------------------------------


def test_cellmarker_helpers_precision_recall() -> None:
    from agentcoop.wrappers.cellmarker_evaluator_local.adapter import (
        _norm_symbol,
        _precision,
        _recall,
        _safe_sub,
        _strict_win,
    )

    pred = {_norm_symbol(g) for g in ["DES", "KRT14", "MYH7"]}
    gold = {_norm_symbol(g) for g in ["DES", "KRT14", "Krt5"]}
    assert _precision(pred, gold) == pytest.approx(2 / 3)
    assert _recall(pred, gold) == pytest.approx(2 / 3)
    assert _precision(set(), gold) is None
    assert _recall(pred, set()) is None
    assert _safe_sub(0.5, 0.2) == pytest.approx(0.3)
    assert _safe_sub(None, 0.2) is None
    assert _strict_win(0.4, 0.1, 0.2) is True
    assert _strict_win(0.1, 0.2, 0.3) is False
    assert _strict_win(None) is None
