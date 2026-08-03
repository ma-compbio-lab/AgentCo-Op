"""Tests for the Session-8 ablation variants (per `docs/experiments/ablation.md`).

The variants are defined in `configs/benchmarks/_base.yaml` and inherit
into every per-dataset config via `extends: _base`. The four ablation
variants are implemented purely with the existing wired knobs in
`agentcoop/benchmarks/runner.py::_apply_variant` (`disable_gates`,
`disable_reviewer`) — no core-code edits.

These tests guard the YAML wiring so a future edit can't silently
remove the ablation variants or change their semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcoop.benchmarks.runner import load_config, _apply_variant
from agentcoop.core.schema import (
    GatePolicy, NodeSpec, EdgeSpec, WorkflowBlueprint, TaskProfile,
    Budget, MemoryPlan, EvalContract,
)


CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "benchmarks"
ABLATION_VARIANTS = ("AC-Full", "AC-NoGate", "AC-NoSkillsTools", "AC-Minimal")


def _make_blueprint() -> WorkflowBlueprint:
    """Tiny blueprint with one reviewer node + one gate."""
    return WorkflowBlueprint(
        blueprint_id="bp-ut-ablation",
        task_profile=TaskProfile(task_id="ut", raw_task="ut"),
        nodes=[
            NodeSpec(node_id="solver", role="solver", backend="llm"),
            NodeSpec(node_id="reviewer", role="reviewer", backend="llm"),
            NodeSpec(node_id="formatter", role="formatter", backend="llm"),
        ],
        edges=[
            EdgeSpec(source="solver", target="reviewer"),
            EdgeSpec(source="reviewer", target="formatter"),
        ],
        gate_policies=[
            GatePolicy(
                name="ut_gate",
                trigger="schema_invalid",
                action="retry_node",
                max_activations=2,
            ),
        ],
        memory_plan=MemoryPlan(),
        eval_contract=EvalContract(),
        budget=Budget(),
    )


@pytest.mark.parametrize("dataset", [
    "gsm8k", "humaneval", "mbpp", "drop", "hotpotqa", "math",
])
@pytest.mark.parametrize("variant", ABLATION_VARIANTS)
def test_ablation_variants_inherit_into_every_dataset(dataset: str, variant: str) -> None:
    cfg = load_config(CONFIG_DIR / f"{dataset}.yaml")
    variants = cfg.get("variants") or {}
    assert variant in variants, (
        f"{variant!r} missing from {dataset}.yaml (must inherit from _base.yaml)"
    )


def test_ac_full_keeps_blueprint_intact() -> None:
    cfg = load_config(CONFIG_DIR / "_base.yaml")
    bp = _make_blueprint()
    n_nodes_before = len(bp.nodes)
    n_max_act_before = bp.gate_policies[0].max_activations
    _apply_variant(bp, cfg["variants"]["AC-Full"])
    assert len(bp.nodes) == n_nodes_before
    assert bp.gate_policies[0].max_activations == n_max_act_before


def test_ac_nogate_zeroes_max_activations() -> None:
    cfg = load_config(CONFIG_DIR / "_base.yaml")
    bp = _make_blueprint()
    _apply_variant(bp, cfg["variants"]["AC-NoGate"])
    assert all(p.max_activations == 0 for p in bp.gate_policies)
    # Reviewer node still present.
    assert any(n.role == "reviewer" for n in bp.nodes)


def test_ac_noskillstools_drops_reviewer_nodes() -> None:
    cfg = load_config(CONFIG_DIR / "_base.yaml")
    bp = _make_blueprint()
    _apply_variant(bp, cfg["variants"]["AC-NoSkillsTools"])
    assert not any(n.role == "reviewer" for n in bp.nodes)
    # Edges through reviewer pruned.
    keep = {n.node_id for n in bp.nodes}
    assert all(e.source in keep and e.target in keep for e in bp.edges)
    # Gates retained.
    assert bp.gate_policies[0].max_activations > 0


def test_ac_minimal_combines_both() -> None:
    cfg = load_config(CONFIG_DIR / "_base.yaml")
    bp = _make_blueprint()
    _apply_variant(bp, cfg["variants"]["AC-Minimal"])
    assert all(p.max_activations == 0 for p in bp.gate_policies)
    assert not any(n.role == "reviewer" for n in bp.nodes)


@pytest.mark.parametrize("variant_file", [
    "ac_full.yaml", "ac_nogate.yaml", "ac_noskillstools.yaml", "ac_minimal.yaml",
])
def test_method_config_files_load_and_carry_expected_factors(variant_file: str) -> None:
    """Sanity-check the documentation-only ablation method-config YAMLs."""
    import yaml

    p = Path(__file__).resolve().parents[2] / "configs" / "ablations" / variant_file
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "method" in data and "ablation_variant" in data
    assert "skills_tools" in data and isinstance(data["skills_tools"], bool)
    assert "gate_repair" in data and isinstance(data["gate_repair"], bool)
    expected = {
        "ac_full.yaml":          (True,  True,  "AC-Full"),
        "ac_nogate.yaml":        (True,  False, "AC-NoGate"),
        "ac_noskillstools.yaml": (False, True,  "AC-NoSkillsTools"),
        "ac_minimal.yaml":       (False, False, "AC-Minimal"),
    }[variant_file]
    assert (data["skills_tools"], data["gate_repair"], data["ablation_variant"]) == expected
