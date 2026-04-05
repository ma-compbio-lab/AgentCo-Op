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
