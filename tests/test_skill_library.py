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
