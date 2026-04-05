import pytest

from agentcoop.workflows.synthesis.component_library import ComponentLibrary, ComponentSpec
from agentcoop.workflows.synthesis.searcher import ComponentSearcher
from agentcoop.workflows.synthesis.validator import BlueprintValidator, BlueprintValidationError
from agentcoop.workflows.design import TaskProfile
from agentcoop.ir.schema import (
    WorkflowBlueprint, TaskSpec, BudgetSpec, NodeSpec, NodeKind, EdgeSpec, ModelSpec, IOContract,
)


def test_component_library_builds_without_error():
    lib = ComponentLibrary()
    assert len(lib.components) > 0


def test_all_components_have_required_fields():
    lib = ComponentLibrary()
    for c in lib.components:
        assert c.component_id
        assert c.source_pattern
        assert c.role
        assert isinstance(c.capability_tags, list)


def test_searcher_returns_top_k():
    lib = ComponentLibrary()
    searcher = ComponentSearcher(lib, top_k=5)
    profile = TaskProfile(domain="math", answer_mode="exact_answer", requires_tool_execution=True)
    results = searcher.search(profile)
    assert len(results) <= 5
    assert all(isinstance(c, ComponentSpec) for c, _ in results)


def test_searcher_code_profile_returns_results():
    lib = ComponentLibrary()
    searcher = ComponentSearcher(lib, top_k=3)
    profile = TaskProfile(domain="coding", answer_mode="code", requires_test_execution=True)
    results = searcher.search(profile)
    # All results should be valid (component, score) tuples
    assert len(results) > 0
    assert all(isinstance(c, ComponentSpec) and isinstance(s, float) for c, s in results)


# ---- BlueprintValidator tests ----

def _minimal_blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        task=TaskSpec(task_id="t1", title="T", description="Test"),
        budget=BudgetSpec(),
        base_nodes=[NodeSpec(
            node_id="solver",
            kind=NodeKind.agent,
            role="Solver",
            description="solve",
            model=ModelSpec(provider="openai", name="gpt-4o-mini"),
            io=IOContract(),
        )],
        base_edges=[],
    )


def test_validator_accepts_valid_blueprint():
    BlueprintValidator().validate(_minimal_blueprint())


def test_validator_rejects_empty_nodes():
    bp = _minimal_blueprint()
    bp.base_nodes = []
    with pytest.raises(BlueprintValidationError, match="no base_nodes"):
        BlueprintValidator().validate(bp)


def test_validator_rejects_unknown_edge_src():
    bp = _minimal_blueprint()
    # Bypass Pydantic's own model validator by mutating after construction
    bp.base_edges = [EdgeSpec(edge_id="e1", src="nonexistent", dst="solver")]
    with pytest.raises(BlueprintValidationError, match="src="):
        BlueprintValidator().validate(bp)


def test_validator_detects_cycle():
    bp = _minimal_blueprint()
    node2 = NodeSpec(
        node_id="node2",
        kind=NodeKind.agent,
        role="Node2",
        description="n2",
        model=ModelSpec(provider="openai", name="gpt-4o-mini"),
        io=IOContract(),
    )
    bp.base_nodes.append(node2)
    bp.base_edges = [
        EdgeSpec(edge_id="e1", src="solver", dst="node2"),
        EdgeSpec(edge_id="e2", src="node2", dst="solver"),
    ]
    with pytest.raises(BlueprintValidationError, match="Cycle detected"):
        BlueprintValidator().validate(bp)


# ---- WorkflowCompiler integration tests ----

from unittest.mock import MagicMock
from agentcoop.workflows.compiler import WorkflowCompiler
from agentcoop.workflows.design import BlueprintCompilerConfig, TaskProfile, GraphSynthesisSpec


def test_compiler_uses_synthesis_when_selected(monkeypatch):
    """WorkflowCompiler calls SynthesisAssembler when synthesis is selected."""
    from agentcoop.workflows.synthesis.assembler import SynthesisAssembler
    from agentcoop.workflows.synthesis.validator import BlueprintValidator

    cfg = BlueprintCompilerConfig(
        enabled=True,
        mode="synthesize_only",
        task_profile=TaskProfile(domain="coding", answer_mode="code", requires_test_execution=True),
        synthesis=GraphSynthesisSpec(enabled=True, component_search_top_k=5),
    )
    compiler = WorkflowCompiler(cfg)

    # Create a fake blueprint that the assembler will "return"
    fake_bp = _minimal_blueprint()

    monkeypatch.setattr(SynthesisAssembler, "assemble", lambda self, ctx, cands: fake_bp)
    monkeypatch.setattr(BlueprintValidator, "validate", lambda self, bp: None)

    blueprint, trace = compiler.compile(
        task=TaskSpec(task_id="t1", title="code task", description="write code"),
        budget=BudgetSpec(),
        meta={},
        model=ModelSpec(provider="openai", name="gpt-4o-mini"),
        review_model=None,
        resolved_config={},
    )
    assert blueprint is not None
    assert "compile_trace" in blueprint.meta
