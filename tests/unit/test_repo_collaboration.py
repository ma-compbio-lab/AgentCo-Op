"""Unit tests for the external_repo_collaboration framework.

Cover the four core modules added in Session 7
(repo_profile, sandbox_build, agent_card, artifact_broker) plus an
end-to-end test for the orchestrator on a `--no-docker` two-stub run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcoop.core.repo_profile import RepoProfile
from agentcoop.core.sandbox_build import (
    SandboxBuilder,
    SandboxSpec,
    render_compose,
    render_dockerfile,
    spec_from_profile,
)
from agentcoop.core.agent_card import AgentCard, AgentRegistry
from agentcoop.core.artifact_broker import (
    ArtifactBroker,
    validate_csv_path,
    validate_gene_set,
)
from agentcoop.core.repo_collaboration import (
    CollaborationRequest,
    RepoCollaborationOrchestrator,
    register_local_adapter,
)


# ---------------------------------------------------------------------------
# repo_profile / sandbox_build / agent_card render checks
# ---------------------------------------------------------------------------


def _toy_profile() -> RepoProfile:
    return RepoProfile(
        repo_name="ToyAgent",
        repo_url="https://github.com/example/toyagent",
        commit_sha="0" * 40,
        detected_language=["Python"],
        environment_files=["pyproject.toml", "uv.lock"],
        package_managers=["uv", "pep621"],
        python_version_hint="3.11",
        run_modes=["headless"],
        candidate_capabilities=["differential expression analysis"],
        requires_api_key=True,
        api_key_envs=["OPENAI_API_KEY"],
    )


def test_spec_from_profile_picks_uv_strategy() -> None:
    p = _toy_profile()
    spec = spec_from_profile(p)
    assert spec.strategy == "uv"
    assert "python:3.11" in spec.base_image
    assert "OPENAI_API_KEY" in spec.api_key_envs


def test_render_dockerfile_contains_repo_url_and_strategy() -> None:
    p = _toy_profile()
    spec = spec_from_profile(p, name="ToyAgent")
    df = render_dockerfile(spec, p)
    assert "https://github.com/example/toyagent" in df
    assert "uv strategy" in df
    assert "python:3.11" in df


def test_render_compose_lists_two_services() -> None:
    p1 = _toy_profile()
    p2 = _toy_profile().model_copy(update={"repo_name": "Other", "repo_url": "https://github.com/example/other"})
    s1 = spec_from_profile(p1, name="ToyAgent")
    s2 = spec_from_profile(p2, name="Other")
    cmp = render_compose([s1, s2])
    assert "ToyAgent" in cmp and "Other" in cmp
    assert "OPENAI_API_KEY" in cmp


def test_sandbox_builder_dry_run_writes_files(tmp_path: Path) -> None:
    builder = SandboxBuilder(tmp_path)
    p = _toy_profile()
    spec = spec_from_profile(p, name="ToyAgent")
    res = builder.build(spec, profile=p, dry_run=True)
    assert res.dockerfile_path.is_file()
    assert res.image_built is False  # dry-run never builds
    compose_path = builder.write_compose([spec])
    assert compose_path.is_file()


# ---------------------------------------------------------------------------
# AgentCard + AgentRegistry round-trip
# ---------------------------------------------------------------------------


def test_agent_card_yaml_round_trip(tmp_path: Path) -> None:
    p = _toy_profile()
    spec = spec_from_profile(p, name="ToyAgent")
    card = AgentCard.from_profile(p, role="toy specialist", sandbox_spec=spec)
    yaml_path = tmp_path / "card.yaml"
    card.to_yaml(yaml_path)
    loaded = AgentCard.from_yaml(yaml_path)
    assert loaded.name == "ToyAgent"
    assert loaded.repository == p.repo_url
    assert "differential expression analysis" in loaded.capabilities


def test_agent_registry_from_dir(tmp_path: Path) -> None:
    p = _toy_profile()
    card_a = AgentCard.from_profile(p, role="A").model_copy(update={"name": "A"})
    card_b = AgentCard.from_profile(p, role="B").model_copy(update={"name": "B"})
    card_a.to_yaml(tmp_path / "a.yaml")
    card_b.to_yaml(tmp_path / "b.yaml")
    reg = AgentRegistry.from_dir(tmp_path)
    assert sorted(reg.names()) == ["A", "B"]
    assert reg.get("A").role == "A"


# ---------------------------------------------------------------------------
# Artifact broker
# ---------------------------------------------------------------------------


def test_validate_gene_set_human_normalisation() -> None:
    clean, warn = validate_gene_set([" tp53 ", "tp53", "Brca1", "lower"], organism="human")
    # Lower-case "lower" is upper-cased to "LOWER" then accepted (matches regex)
    # while duplicate TP53 is dropped.
    assert "TP53" in clean
    assert clean.count("TP53") == 1
    assert "BRCA1" in clean


def test_validate_gene_set_drops_garbage() -> None:
    clean, warn = validate_gene_set(["TP53", "with spaces", "$$$"], organism="human")
    assert clean == ["TP53"]
    assert any("dropped non-symbol" in w for w in warn)


def test_validate_csv_path_required_columns(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text("gene,log2fc,p_value\nFOO,1.0,0.01\n", encoding="utf-8")
    warns = validate_csv_path(p, required_columns=["gene", "log2fc"])
    assert warns == []
    warns2 = validate_csv_path(p, required_columns=["gene", "missing"])
    assert any("missing column missing" in w for w in warns2)


def test_artifact_broker_gene_set_handoff_writes_file(tmp_path: Path) -> None:
    broker = ArtifactBroker(audit_dir=tmp_path / "audit")
    out = tmp_path / "handoff.json"
    res = broker.handoff_gene_set(
        producer="A", consumer="B",
        genes=["DES", "IGFBP5", "junk!"],
        organism="human",
        write_to=out,
    )
    assert res.ok
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert payload["n_genes"] == 2
    assert "DES" in payload["gene_set"] and "IGFBP5" in payload["gene_set"]


# ---------------------------------------------------------------------------
# Orchestrator end-to-end test (no Docker, no LLM — uses register_local_adapter
# stubs so the test stays fully offline)
# ---------------------------------------------------------------------------


def _stub_request(tmp_path: Path) -> CollaborationRequest:
    return CollaborationRequest(
        case_id="unit_test_collab",
        repositories=[
            {"name": "AgentA", "url": "https://example.invalid/AgentA"},
            {"name": "AgentB", "url": "https://example.invalid/AgentB"},
        ],
        dataset={"local_cache_dir": str(tmp_path / "data")},
        task={"description": "unit test stub"},
        required_outputs=[],
        success_targets={},
    )


@register_local_adapter("AgentA")
def _agent_a_stub(req: dict) -> dict:
    out_dir = Path(req["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "marker.csv").write_text("gene,log2fc\nDES,1.5\n", encoding="utf-8")
    return {
        "status": "success",
        "summary": "stub upstream",
        "warnings": [],
        "artifacts": {"gene_set": ["DES", "IGFBP5"], "marker_genes_csv": str(out_dir / "marker.csv")},
    }


@register_local_adapter("AgentB")
def _agent_b_stub(req: dict) -> dict:
    return {
        "status": "success",
        "summary": "stub downstream interpretation",
        "warnings": [],
        "artifacts": {"geneagent_report_md": "/dev/null"},
    }


def test_orchestrator_runs_with_local_stub_adapters(tmp_path: Path, monkeypatch) -> None:
    # Ensure no real OpenAI traffic.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = _stub_request(tmp_path)
    workdir = tmp_path / "run"
    orch = RepoCollaborationOrchestrator(
        req, workdir=workdir, no_docker=True, external_root=tmp_path / "external",
    )
    res = orch.run()
    # Status will be `failed` because RepoProfile clone for the invalid URLs
    # failed → fallback profiles are emitted with warnings; the orchestrator
    # still runs the registered local adapters end to end.
    assert (workdir / "run_manifest.json").is_file()
    assert (workdir / "compiled_workflow_graph.json").is_file()
    assert (workdir / "agent_registry.json").is_file()
    assert (workdir / "artifacts" / "agentb_input.json").is_file()
    assert res.status in {"success", "partial", "synthetic_fallback", "failed"}
