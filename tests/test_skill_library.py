from agentcoop.runtime.skills import LoadedSkill


def test_loaded_skill_type_defaults_to_agent_skill():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={})
    assert skill.skill_type == "agent-skill"


def test_loaded_skill_type_from_metadata():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={"type": "meta-skill"})
    assert skill.skill_type == "meta-skill"


def test_loaded_skill_domain_tags():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={
        "domain_tags": ["spatial", "transcriptomics"]
    })
    assert skill.domain_tags == ["spatial", "transcriptomics"]


def test_loaded_skill_domain_tags_default_empty():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={})
    assert skill.domain_tags == []


def test_loaded_skill_capability_tags():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={
        "capability_tags": ["data_analysis", "visualization"]
    })
    assert skill.capability_tags == ["data_analysis", "visualization"]


def test_loaded_skill_is_shareable_default_false():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={})
    assert skill.shareable is False


def test_loaded_skill_is_shareable_true():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={"shareable": True})
    assert skill.shareable is True


def test_loaded_skill_roles_empty_for_agent_skill():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={"type": "agent-skill"})
    assert skill.roles == []


def test_loaded_skill_roles_from_meta_skill():
    skill = LoadedSkill(name="test", path="/tmp/SKILL.md", description="", body="", metadata={
        "type": "meta-skill",
        "roles": [
            {"role": "Analyzer", "description": "Analyze data", "skill_tags": ["analysis"]},
            {"role": "Validator", "description": "Validate", "skill_tags": ["validation"]},
        ],
        "edges": [{"src": "Analyzer", "dst": "Validator"}],
    })
    assert len(skill.roles) == 2
    assert skill.roles[0]["role"] == "Analyzer"
    assert skill.edges == [{"src": "Analyzer", "dst": "Validator"}]


import textwrap
from pathlib import Path
from agentcoop.skills.library import SkillLibrary


def _write_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return skill_path


def test_skill_library_indexes_agent_and_meta_skills(tmp_path):
    _write_skill(tmp_path, "my-agent-skill", """
        ---
        name: my-agent-skill
        type: agent-skill
        description: A test agent skill
        domain_tags: [math, reasoning]
        capability_tags: [solver, verification]
        ---
        Solve math problems step by step.
    """)
    _write_skill(tmp_path, "my-meta-skill", """
        ---
        name: my-meta-skill
        type: meta-skill
        description: A test meta skill
        domain_tags: [math]
        capability_tags: [pipeline]
        roles:
          - role: Solver
            description: Solve the problem
            skill_tags: [math, solver]
          - role: Checker
            description: Check the solution
            skill_tags: [verification]
        edges:
          - src: Solver
            dst: Checker
        ---
        Two-step math pipeline.
    """)

    lib = SkillLibrary(search_paths=[tmp_path])
    assert len(lib.agent_skills) == 1
    assert len(lib.meta_skills) == 1
    assert lib.agent_skills[0].name == "my-agent-skill"
    assert lib.meta_skills[0].name == "my-meta-skill"


def test_skill_library_search_meta_skills(tmp_path):
    _write_skill(tmp_path, "math-pipeline", """
        ---
        name: math-pipeline
        type: meta-skill
        description: Math problem solving pipeline
        domain_tags: [math, reasoning]
        capability_tags: [pipeline, verification]
        roles:
          - role: Solver
            description: Solve
            skill_tags: [math]
        edges: []
        ---
        Solve math.
    """)
    _write_skill(tmp_path, "code-pipeline", """
        ---
        name: code-pipeline
        type: meta-skill
        description: Code generation and testing pipeline
        domain_tags: [coding, testing]
        capability_tags: [code, repair]
        roles:
          - role: Coder
            description: Write code
            skill_tags: [code]
        edges: []
        ---
        Write code.
    """)

    lib = SkillLibrary(search_paths=[tmp_path])
    results = lib.search_meta_skills("math reasoning verification")
    assert len(results) >= 1
    assert results[0][0].name == "math-pipeline"


def test_skill_library_search_agent_skills(tmp_path):
    _write_skill(tmp_path, "data-loader", """
        ---
        name: data-loader
        type: agent-skill
        description: Load and preprocess data
        domain_tags: [data]
        capability_tags: [loading, preprocessing]
        ---
        Load data carefully.
    """)
    _write_skill(tmp_path, "visualizer", """
        ---
        name: visualizer
        type: agent-skill
        description: Create visualizations and figures
        domain_tags: [visualization]
        capability_tags: [plotting, figures]
        ---
        Make good plots.
    """)

    lib = SkillLibrary(search_paths=[tmp_path])
    results = lib.search_agent_skills("loading preprocessing data")
    assert len(results) >= 1
    assert results[0][0].name == "data-loader"


from agentcoop.skills.assembler import SkillDrivenAssembler
from agentcoop.ir.schema import ModelSpec, TaskSpec, BudgetSpec, NodeKind


def test_assembler_builds_blueprint_from_meta_and_agent_skills(tmp_path):
    _write_skill(tmp_path, "test-pipeline", """
        ---
        name: test-pipeline
        type: meta-skill
        description: Two-step pipeline
        domain_tags: [testing]
        capability_tags: [pipeline]
        roles:
          - role: Worker
            description: Do the work
            skill_tags: [work, execution]
            kind: agent
          - role: Checker
            description: Check the work
            skill_tags: [validation, checking]
            kind: evaluator
        edges:
          - src: Worker
            dst: Checker
        ---
        A simple two-step pipeline.
    """)
    _write_skill(tmp_path, "work-skill", """
        ---
        name: work-skill
        type: agent-skill
        description: Execute work tasks
        domain_tags: [work]
        capability_tags: [work, execution]
        ---
        Execute the assigned task carefully.
    """)
    _write_skill(tmp_path, "check-skill", """
        ---
        name: check-skill
        type: agent-skill
        description: Validate and check results
        domain_tags: [validation]
        capability_tags: [validation, checking]
        ---
        Validate the results thoroughly.
    """)

    lib = SkillLibrary(search_paths=[tmp_path])
    meta_skill = lib.meta_skills[0]
    assembler = SkillDrivenAssembler(library=lib, max_skills_per_agent=3)

    blueprint = assembler.assemble(
        meta_skill=meta_skill,
        task=TaskSpec(task_id="t1", title="Test", description="Test task"),
        budget=BudgetSpec(),
        model=ModelSpec(provider="openai", name="gpt-4o-mini"),
        review_model=ModelSpec(provider="openai", name="gpt-4o-mini"),
    )

    assert len(blueprint.base_nodes) == 2
    assert len(blueprint.base_edges) == 1
    node_ids = [n.node_id for n in blueprint.base_nodes]
    assert "worker" in node_ids
    assert "checker" in node_ids
    worker = next(n for n in blueprint.base_nodes if n.node_id == "worker")
    assert len(worker.skills) >= 1
    assert worker.kind == NodeKind.agent
    checker = next(n for n in blueprint.base_nodes if n.node_id == "checker")
    assert len(checker.skills) >= 1
    assert checker.kind == NodeKind.evaluator


def test_assembler_deduplicates_non_shareable_skills(tmp_path):
    _write_skill(tmp_path, "pipeline", """
        ---
        name: pipeline
        type: meta-skill
        description: Pipeline
        domain_tags: [generic]
        capability_tags: [pipeline]
        roles:
          - role: A
            description: First agent
            skill_tags: [generic, work]
            kind: agent
          - role: B
            description: Second agent
            skill_tags: [generic, work]
            kind: agent
        edges:
          - src: A
            dst: B
        ---
        Generic pipeline.
    """)
    _write_skill(tmp_path, "only-skill", """
        ---
        name: only-skill
        type: agent-skill
        description: The only available skill
        domain_tags: [generic]
        capability_tags: [generic, work]
        shareable: false
        ---
        Do work.
    """)

    lib = SkillLibrary(search_paths=[tmp_path])
    assembler = SkillDrivenAssembler(library=lib, max_skills_per_agent=3, deduplicate=True)
    blueprint = assembler.assemble(
        meta_skill=lib.meta_skills[0],
        task=TaskSpec(task_id="t1", title="T", description="T"),
        budget=BudgetSpec(),
        model=ModelSpec(provider="openai", name="gpt-4o-mini"),
        review_model=ModelSpec(provider="openai", name="gpt-4o-mini"),
    )
    node_a = next(n for n in blueprint.base_nodes if n.node_id == "a")
    node_b = next(n for n in blueprint.base_nodes if n.node_id == "b")
    a_skill_names = [s.name for s in node_a.skills]
    b_skill_names = [s.name for s in node_b.skills]
    # only-skill assigned to first role (A) only, not B
    assert "only-skill" in a_skill_names
    assert "only-skill" not in b_skill_names


from agentcoop.skills.bootstrapper import SkillBootstrapper


def test_bootstrapper_generates_meta_skills(tmp_path):
    bootstrapper = SkillBootstrapper(output_dir=tmp_path)
    generated = bootstrapper.bootstrap_all()
    assert len(generated) >= 1
    for path in generated:
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "type: meta-skill" in content
        assert "roles:" in content


def test_bootstrapped_meta_skill_has_valid_roles(tmp_path):
    bootstrapper = SkillBootstrapper(output_dir=tmp_path)
    generated = bootstrapper.bootstrap_all()
    from agentcoop.runtime.skills import SkillRegistry
    registry = SkillRegistry(search_paths=[str(tmp_path)])
    registry._ensure_index()
    for path_list in registry._paths_by_name.values():
        for path in path_list:
            skill = registry._load_skill(path)
            if skill.skill_type == "meta-skill":
                assert len(skill.roles) >= 1
                for role in skill.roles:
                    assert "role" in role
                    assert "description" in role
                    assert "skill_tags" in role
                return
    raise AssertionError("No meta-skill found in bootstrapped output")


from agentcoop.workflows.compiler import WorkflowCompiler
from agentcoop.workflows.design import BlueprintCompilerConfig, TaskProfile, SkillDrivenSpec
from agentcoop.ir.schema import TaskSpec, BudgetSpec, ModelSpec


def test_compiler_node_skills_attaches_configured_skills_by_node_id():
    cfg = BlueprintCompilerConfig(
        enabled=True,
        mode="pattern_library",
        preferred_pattern="reason_execute_select",
        task_profile=TaskProfile(
            domain="math",
            answer_mode="exact_answer",
            requires_tool_execution=True,
        ),
        meta_skill_hint="math-scensemble-workflow",
        node_skills={
            "solver": ["math-reasoning-discipline", "algebra-tactics"],
            "programmer": ["python-sympy-programmer"],
            "selector": ["ensemble-arbitration"],
        },
        pattern_overrides={
            "reason_execute_select": {
                "enable_challenger_solver": True,
                "tool_nodes": {
                    "program_exec": {
                        "meta": {"direct_sandbox_handler": True},
                        "sandbox": {"sandbox_id": "probe", "image": "python:3.11-slim"},
                    }
                },
            }
        },
    )
    compiler = WorkflowCompiler(cfg)
    blueprint, _trace = compiler.compile(
        task=TaskSpec(task_id="t", title="t", description="solve the math problem"),
        budget=BudgetSpec(),
        meta={},
        model=ModelSpec(provider="openai", name="gpt-4o-mini"),
        review_model=None,
        resolved_config={},
    )
    nodes = {n.node_id: n for n in blueprint.base_nodes}
    solver_skills = {s.name for s in nodes["solver"].skills}
    assert "math-reasoning-discipline" in solver_skills
    assert "algebra-tactics" in solver_skills
    assert "structured-json-discipline" in solver_skills  # default still applied
    programmer_skills = {s.name for s in nodes["programmer"].skills}
    assert "python-sympy-programmer" in programmer_skills
    selector_skills = {s.name for s in nodes["selector"].skills}
    assert "ensemble-arbitration" in selector_skills
    bindings = blueprint.meta["skill_bindings"]
    assert bindings["meta_skill_hint"] == "math-scensemble-workflow"
    assert "math-reasoning-discipline" in bindings["node_skills"]["solver"]


def test_skills_for_node_falls_back_to_role():
    cfg = BlueprintCompilerConfig(node_skills={"Solver": ["algebra-tactics"]})
    assert cfg.skills_for_node(node_id="different_id", role="Solver") == ["algebra-tactics"]
    assert cfg.skills_for_node(node_id="different_id", role="solver") == ["algebra-tactics"]
    assert cfg.skills_for_node(node_id="unknown", role=None) == []


def test_bundled_two_level_skills_are_indexed():
    """Smoke test: the bundled meta- and agent-skills must be resolvable by the SkillRegistry."""
    from agentcoop.runtime.skills import SkillRegistry

    registry = SkillRegistry()
    registry._ensure_index()
    names = set(registry._paths_by_name.keys())
    expected_meta = {
        "qa-multihop-workflow",
        "qa-extractive-workflow",
        "math-scensemble-workflow",
        "code-test-repair-workflow",
        "numerical-rc-workflow",
    }
    expected_agent = {
        "algebra-tactics",
        "calculus-tactics",
        "number-theory-tactics",
        "combinatorics-tactics",
        "squad-answer-extraction",
        "bridging-inference",
        "test-driven-repair",
        "edge-case-enumeration",
        "date-arithmetic",
        "count-sort-aggregate",
        "ensemble-arbitration",
        "python-sympy-programmer",
        # Failure-distilled skills (from benchmark log analysis)
        "answer-form-faithful",
        "sympy-type-hygiene",
        "numeric-sanity-guard",
        "executed-answer-sanity-gate",
        "constraint-quantifier-scan",
        "ceiling-floor-discipline",
        "spec-example-cross-check",
        "return-shape-contract",
        "signature-fidelity-guard",
        "entity-type-gate",
        "drop-answer-type-protocol",
        "asymptote-figure-reader",
    }
    missing_meta = expected_meta - names
    missing_agent = expected_agent - names
    assert not missing_meta, f"Missing meta-skills: {missing_meta}"
    assert not missing_agent, f"Missing agent-skills: {missing_agent}"


def test_compiler_skill_driven_path(tmp_path):
    _write_skill(tmp_path, "my-pipeline", """
        ---
        name: my-pipeline
        type: meta-skill
        description: Simple pipeline for testing
        domain_tags: [testing]
        capability_tags: [pipeline, testing]
        roles:
          - role: Worker
            description: Do work
            skill_tags: [work]
            kind: agent
        edges: []
        ---
        Test pipeline.
    """)
    _write_skill(tmp_path, "work-agent", """
        ---
        name: work-agent
        type: agent-skill
        description: Agent that does work
        domain_tags: [work]
        capability_tags: [work, execution]
        ---
        Work hard.
    """)

    cfg = BlueprintCompilerConfig(
        enabled=True,
        mode="pattern_then_synthesize",
        task_profile=TaskProfile(domain="testing"),
        skill_driven=SkillDrivenSpec(
            enabled=True,
            skill_search_paths=[str(tmp_path)],
            min_meta_skill_score=0.01,
        ),
    )
    compiler = WorkflowCompiler(cfg)
    blueprint, trace = compiler.compile(
        task=TaskSpec(task_id="t1", title="Test", description="testing pipeline work"),
        budget=BudgetSpec(),
        meta={},
        model=ModelSpec(provider="openai", name="gpt-4o-mini"),
        review_model=None,
        resolved_config={},
    )
    assert blueprint is not None
    assert blueprint.meta.get("skill_driven") is True
    assert "compile_trace" in blueprint.meta
