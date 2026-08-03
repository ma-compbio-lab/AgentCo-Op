"""Unit tests for the Session-7.2 additions:
- env_manager (auto pip install + manifest)
- topology_viz (PNG + DOT)
- collab_report (collaboration_log.md + final_report.md)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcoop.core.env_manager import (
    EnvManager,
    PackageRequirement,
    ensure_env_for_agents,
    register_required_packages,
    required_packages_for,
)
from agentcoop.core.topology_viz import render_topology
from agentcoop.core.collab_report import (
    write_collaboration_log,
    write_final_report,
)


# ---------------------------------------------------------------------------
# env_manager
# ---------------------------------------------------------------------------


def test_register_and_query_requirements() -> None:
    register_required_packages("ToyAgent", ["numpy", ("PyYAML", "yaml")])
    reqs = required_packages_for("toyagent")
    names = {r.name for r in reqs}
    assert "numpy" in names
    assert "PyYAML" in names
    yaml_req = next(r for r in reqs if r.name == "PyYAML")
    assert yaml_req.probe_name == "yaml"


def test_env_manager_detects_already_present() -> None:
    mgr = EnvManager(mode="local")
    # `json` ships with the stdlib; numpy is in our test env.
    status = mgr.ensure_one(PackageRequirement(name="numpy"))
    assert status.state == "already_present"
    assert status.installed_version


def test_env_manager_docker_mode_skips() -> None:
    mgr = EnvManager(mode="docker")
    status = mgr.ensure_one(PackageRequirement(name="some-package-that-does-not-matter"))
    assert status.state == "skipped_docker"


def test_ensure_env_for_agents_writes_manifest(tmp_path: Path) -> None:
    register_required_packages("UnitTestAgent", ["numpy"])
    ensure_env_for_agents(["UnitTestAgent"], workdir=tmp_path, mode="local")
    manifest = tmp_path / "manifests" / "env_manifest.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text())
    assert data["mode"] == "local"
    assert any(p["name"] == "numpy" for p in data["packages"])


# ---------------------------------------------------------------------------
# topology_viz
# ---------------------------------------------------------------------------


def _fake_graph() -> dict:
    return {
        "case_id": "unit_test",
        "topology_type": "external_repo_collaboration_l7",
        "nodes": [
            {"id": "repo_profiler", "kind": "agentcoop_internal", "role": "inspect repos"},
            {"id": "AgentA_run", "kind": "sandboxed_external_agent", "agent": "AgentA", "role": "primary"},
            {"id": "broker", "kind": "agentcoop_internal", "role": "validate handoff"},
            {"id": "AgentB_run", "kind": "sandboxed_external_agent", "agent": "AgentB", "role": "interpret"},
            {"id": "integrator", "kind": "llm_backed_agent_node", "role": "synthesize"},
        ],
        "edges": [
            ["repo_profiler", "AgentA_run"],
            ["AgentA_run", "broker"],
            ["broker", "AgentB_run"],
            ["AgentB_run", "integrator"],
        ],
    }


def test_render_topology_writes_png_and_dot(tmp_path: Path) -> None:
    arts = render_topology(_fake_graph(), out_dir=tmp_path, title="Unit test")
    assert Path(arts["topology_png"]).is_file()
    assert Path(arts["topology_dot"]).is_file()
    dot_text = Path(arts["topology_dot"]).read_text()
    assert "digraph G" in dot_text
    assert "AgentA_run" in dot_text


# ---------------------------------------------------------------------------
# collab_report
# ---------------------------------------------------------------------------


def _setup_run_dir(tmp_path: Path) -> dict:
    """Build a minimal artifact tree the report writers expect."""
    geneagent = tmp_path / "artifacts" / "geneagent_run"
    integration = tmp_path / "artifacts" / "integration"
    geneagent.mkdir(parents=True, exist_ok=True)
    integration.mkdir(parents=True, exist_ok=True)
    (geneagent / "geneagent_report.md").write_text("# GeneAgent stub\n", encoding="utf-8")
    (integration / "final_hypothesis_report.md").write_text("# Hypothesis\n", encoding="utf-8")
    return {
        "geneagent_md": geneagent / "geneagent_report.md",
        "integrator_md": integration / "final_hypothesis_report.md",
    }


def test_write_collaboration_log_and_final_report(tmp_path: Path) -> None:
    artifacts = _setup_run_dir(tmp_path)
    repos = {
        "AgentA": {"url": "https://example/A", "commit": "deadbeef" * 5,
                   "container": "agentcoop-agenta:t", "wrapper_strategy": "headless_python"},
        "AgentB": {"url": "https://example/B", "commit": "feedfacef" * 5,
                   "container": "agentcoop-agentb:t", "wrapper_strategy": "headless_python"},
    }
    upstream_resp = {
        "status": "success", "summary": "ran A",
        "artifacts": {"gene_set": ["DES", "MYH7"], "marker_genes_csv": str(tmp_path / "m.csv")},
        "main_results": {"marker_count": 2},
    }
    downstream_resp = {
        "status": "success", "summary": "ran B",
        "artifacts": {"geneagent_report_md": str(artifacts["geneagent_md"])},
    }
    handoffs = [{
        "ok": True, "warnings": [],
        "artifact": {"kind": "gene_set", "name": "AgentA_to_AgentB_gene_set",
                     "path": str(tmp_path / "h.json")},
    }]
    integrator_meta = {"model": "gpt-5", "reasoning_effort": "medium",
                       "tokens_in": 100, "tokens_out": 200}
    log_path = write_collaboration_log(
        workdir=tmp_path, case_id="ut",
        repos=repos, upstream_name="AgentA", downstream_name="AgentB",
        upstream_resp=upstream_resp, downstream_resp=downstream_resp,
        handoffs=handoffs, integrator_meta=integrator_meta,
        env_report=None, sandbox_report={"AgentA": {"image_built": False, "notes": []}},
        elapsed_s=12.3, status="success", notes=["unit test"],
    )
    assert log_path.is_file()
    log_text = log_path.read_text()
    assert "Stage" not in log_text or "stage" in log_text.lower()
    assert "AgentA" in log_text and "AgentB" in log_text

    final_path = write_final_report(
        workdir=tmp_path, case_id="ut",
        repos=repos, upstream_name="AgentA", downstream_name="AgentB",
        upstream_resp=upstream_resp, downstream_resp=downstream_resp,
        handoffs=handoffs, integrator_meta=integrator_meta,
        integrator_md_path=artifacts["integrator_md"],
        geneagent_md_path=artifacts["geneagent_md"],
        topology_artifacts={}, env_report=None,
        elapsed_s=12.3, status="success", raw_artifacts_root=tmp_path,
        notes=["unit test"],
    )
    assert final_path.is_file()
    final_text = final_path.read_text()
    assert "AgentCo-Op final report" in final_text
    assert "AgentA" in final_text and "AgentB" in final_text
    assert "GeneAgent stub" in final_text  # embedded
    assert "Hypothesis" in final_text       # embedded
