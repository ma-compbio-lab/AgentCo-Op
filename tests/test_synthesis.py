from dynaforge.workflows.synthesis.component_library import ComponentLibrary, ComponentSpec
from dynaforge.workflows.synthesis.searcher import ComponentSearcher
from dynaforge.workflows.design import TaskProfile


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
