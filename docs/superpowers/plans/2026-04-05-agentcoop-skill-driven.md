# AgentCo-Op Skill-Driven Compilation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename framework from DynaForge to AgentCo-Op and implement skill-driven workflow compilation where meta-skills define topology and agent-skills specialize each node.

**Architecture:** Two-tier skill system (meta-skills for topology, agent-skills for behavior) with BM25 search over a unified skill library. The compiler tries skill-driven assembly first, falls back to existing pattern/synthesis path. Existing patterns are bootstrapped into meta-skills.

**Tech Stack:** Python 3.10+, Pydantic v2, existing BM25 implementation (stdlib only), PyYAML for SKILL.md parsing.

---

## File Map

### Sub-project A: Rename

**Modify (mechanical find-replace):**
- `pyproject.toml` — package name, entrypoints, package-data
- `src/dynaforge/` → `src/agentcoop/` (entire directory renamed)
- All `*.py` files under `src/` and `tests/` — imports
- `scripts/*.py` — sys.path and imports
- `README.md`, `CLAUDE.md` — documentation
- `.gitignore` — artifact directory

### Sub-project B: Skill-Driven Compilation

**Create:**
- `src/agentcoop/skills/library.py` — `SkillLibrary` with BM25 search
- `src/agentcoop/skills/bootstrapper.py` — `SkillBootstrapper` generates meta-skills from patterns
- `src/agentcoop/skills/assembler.py` — `SkillDrivenAssembler` assembles blueprint from skills
- `src/agentcoop/bootstrap_cli.py` — `agentcoop-bootstrap-skills` CLI
- `skills/meta-skills/*/SKILL.md` — bootstrapped meta-skill files
- `tests/test_skill_library.py` — tests for library, assembler, bootstrapper

**Modify:**
- `src/agentcoop/workflows/design.py` — add `SkillDrivenSpec`
- `src/agentcoop/workflows/compiler.py` — add `_try_skill_driven()` path
- `src/agentcoop/runtime/skills.py` — extend `LoadedSkill` with new field accessors

---

## Sub-project A: Rename dynaforge → agentcoop

---

### Task 1: Rename package directory and update all imports

**Files:**
- Rename: `src/dynaforge/` → `src/agentcoop/`
- Modify: `pyproject.toml`
- Modify: all `*.py` files under `src/agentcoop/`, `tests/`, `scripts/`

- [ ] **Step 1: Rename the package directory**

```bash
cd /path/to/worktree
git mv src/dynaforge src/agentcoop
```

- [ ] **Step 2: Update `pyproject.toml`**

Replace all occurrences of `dynaforge` with `agentcoop`:

```toml
[project]
name = "agentcoop"
description = "Skill-driven workflow compiler/runtime for specialized agent collaboration."

[project.scripts]
agentcoop-run = "agentcoop.cli:main"
agentcoop-exp = "agentcoop.experiment_cli:main"

[tool.setuptools.package-data]
agentcoop = ["conf/**/*.yaml", "skills/**/*.md"]
```

- [ ] **Step 3: Find-and-replace all Python imports**

```bash
# Replace in all Python files under src/
find src/ -name '*.py' -exec sed -i '' 's/from dynaforge\./from agentcoop./g' {} +
find src/ -name '*.py' -exec sed -i '' 's/import dynaforge\./import agentcoop./g' {} +
find src/ -name '*.py' -exec sed -i '' 's/import dynaforge$/import agentcoop/g' {} +
find src/ -name '*.py' -exec sed -i '' 's/"dynaforge\./"agentcoop./g' {} +

# Replace in tests
find tests/ -name '*.py' -exec sed -i '' 's/from dynaforge\./from agentcoop./g' {} +
find tests/ -name '*.py' -exec sed -i '' 's/import dynaforge\./import agentcoop./g' {} +
find tests/ -name '*.py' -exec sed -i '' 's/import dynaforge$/import agentcoop/g' {} +
find tests/ -name '*.py' -exec sed -i '' 's/"dynaforge\./"agentcoop./g' {} +

# Replace in scripts
find scripts/ -name '*.py' -exec sed -i '' 's/from dynaforge\./from agentcoop./g' {} +
find scripts/ -name '*.py' -exec sed -i '' 's/import dynaforge\./import agentcoop./g' {} +
```

- [ ] **Step 4: Update environment variable name**

In `src/agentcoop/runtime/skills.py`, replace:
```python
DEFAULT_SKILL_PATH_ENV = "DYNAFORGE_SKILL_PATHS"
```
with:
```python
DEFAULT_SKILL_PATH_ENV = "AGENTCOOP_SKILL_PATHS"
```

- [ ] **Step 5: Update artifact directory default**

In `src/agentcoop/conf/config.yaml`, replace:
```yaml
artifact_root: .dynaforge_artifacts
```
with:
```yaml
artifact_root: .agentcoop_artifacts
```

Also in `src/agentcoop/runtime/executor.py`, update the default:
```python
artifact_root: str | Path = ".agentcoop_artifacts",
```

- [ ] **Step 6: Update `.gitignore`**

Replace `.dynaforge_artifacts` with `.agentcoop_artifacts`.

- [ ] **Step 7: Update string references in docs**

In `README.md` and `CLAUDE.md`:
- Replace `dynaforge-run` → `agentcoop-run`
- Replace `dynaforge-exp` → `agentcoop-exp`
- Replace `DynaForge` → `AgentCo-Op`
- Replace `dynaforge` → `agentcoop` in code paths

- [ ] **Step 8: Update remaining string literals**

Search for any remaining `dynaforge` string references:
```bash
grep -rn "dynaforge" src/ tests/ scripts/ pyproject.toml README.md CLAUDE.md --include="*.py" --include="*.yaml" --include="*.toml" --include="*.md"
```
Fix any remaining hits.

- [ ] **Step 9: Re-install and run tests**

```bash
pip install -e ".[dev]"
pytest tests/ -q 2>&1 | tail -10
```
Expected: all tests pass (same count as before).

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "[refactor] rename dynaforge to agentcoop

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Sub-project B: Skill-Driven Compilation

---

### Task 2: Extend `LoadedSkill` with new field accessors

**Files:**
- Modify: `src/agentcoop/runtime/skills.py`
- Test: `tests/test_skill_library.py` (create)

- [ ] **Step 1: Write tests for new field accessors**

Create `tests/test_skill_library.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skill_library.py -v`
Expected: FAIL — `LoadedSkill` has no `skill_type`, `domain_tags`, etc.

- [ ] **Step 3: Add property accessors to `LoadedSkill`**

In `src/agentcoop/runtime/skills.py`, add properties to `LoadedSkill` (after the existing `allowed_tools` property):

```python
@dataclass(frozen=True)
class LoadedSkill:
    name: str
    path: str
    description: str
    body: str
    metadata: JsonDict

    @property
    def allowed_tools(self) -> list[str]:
        raw = self.metadata.get("allowed_tools", [])
        if isinstance(raw, str):
            value = raw.strip()
            return [value] if value else []
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return []

    @property
    def skill_type(self) -> str:
        return str(self.metadata.get("type", "agent-skill")).strip()

    @property
    def domain_tags(self) -> list[str]:
        raw = self.metadata.get("domain_tags", [])
        return [str(t).strip() for t in raw] if isinstance(raw, list) else []

    @property
    def capability_tags(self) -> list[str]:
        raw = self.metadata.get("capability_tags", [])
        return [str(t).strip() for t in raw] if isinstance(raw, list) else []

    @property
    def shareable(self) -> bool:
        return bool(self.metadata.get("shareable", False))

    @property
    def roles(self) -> list[JsonDict]:
        if self.skill_type != "meta-skill":
            return []
        raw = self.metadata.get("roles", [])
        return list(raw) if isinstance(raw, list) else []

    @property
    def edges(self) -> list[JsonDict]:
        if self.skill_type != "meta-skill":
            return []
        raw = self.metadata.get("edges", [])
        return list(raw) if isinstance(raw, list) else []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_skill_library.py -v`
Expected: all 9 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/agentcoop/runtime/skills.py tests/test_skill_library.py
git commit -m "[feat] extend LoadedSkill with type, tags, roles, edges accessors

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Create `SkillLibrary` with BM25 search

**Files:**
- Create: `src/agentcoop/skills/library.py`
- Test: `tests/test_skill_library.py` (append)

- [ ] **Step 1: Write tests for SkillLibrary**

Append to `tests/test_skill_library.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skill_library.py::test_skill_library_indexes_agent_and_meta_skills -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentcoop.skills.library'`

- [ ] **Step 3: Create `src/agentcoop/skills/__init__.py`**

Check if it exists. If `src/agentcoop/skills/` is just a data directory (containing `structured_json_discipline/`), it may lack `__init__.py`. Create it:

```python
# src/agentcoop/skills/__init__.py
```

- [ ] **Step 4: Create `src/agentcoop/skills/library.py`**

```python
# src/agentcoop/skills/library.py
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agentcoop.runtime.skills import LoadedSkill, SkillRegistry

JsonDict = Dict[str, Any]


def _tokenize(text: str) -> List[str]:
    return text.lower().replace("_", " ").replace("-", " ").split()


class _BM25Index:
    """Minimal BM25 index over tokenized documents."""
    K1 = 1.5
    B = 0.75

    def __init__(self, corpus: List[List[str]]) -> None:
        self._corpus = corpus
        self._avg_dl = sum(len(doc) for doc in corpus) / max(len(corpus), 1)
        n = len(corpus)
        df: Counter = Counter()
        for doc in corpus:
            df.update(set(doc))
        self._idf = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

    def score(self, query_terms: List[str], doc_idx: int) -> float:
        doc = self._corpus[doc_idx]
        dl = len(doc)
        tf_map = Counter(doc)
        total = 0.0
        for term in query_terms:
            idf = self._idf.get(term, 0.0)
            if idf == 0.0:
                continue
            tf = tf_map.get(term, 0)
            numerator = tf * (self.K1 + 1)
            denominator = tf + self.K1 * (1 - self.B + self.B * dl / self._avg_dl)
            total += idf * numerator / denominator
        return total

    def search(self, query: str, top_k: int = 10) -> List[Tuple[int, float]]:
        query_terms = _tokenize(query)
        scored = [(i, self.score(query_terms, i)) for i in range(len(self._corpus))]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class SkillLibrary:
    """Unified index over agent-skills and meta-skills with BM25 search."""

    def __init__(
        self,
        search_paths: Optional[Sequence[Path]] = None,
        *,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self._search_paths = [Path(p) for p in (search_paths or [])]
        self._cache_dir = cache_dir
        self._agent_skills: List[LoadedSkill] = []
        self._meta_skills: List[LoadedSkill] = []
        self._agent_index: Optional[_BM25Index] = None
        self._meta_index: Optional[_BM25Index] = None
        self._scan()

    def _scan(self) -> None:
        registry = SkillRegistry(search_paths=[str(p) for p in self._search_paths])
        registry._ensure_index()

        for path_list in registry._paths_by_name.values():
            for path in path_list:
                skill = registry._load_skill(path)
                if skill.skill_type == "meta-skill":
                    self._meta_skills.append(skill)
                else:
                    self._agent_skills.append(skill)

    @property
    def agent_skills(self) -> List[LoadedSkill]:
        return list(self._agent_skills)

    @property
    def meta_skills(self) -> List[LoadedSkill]:
        return list(self._meta_skills)

    def _ensure_agent_index(self) -> _BM25Index:
        if self._agent_index is None:
            corpus = [
                _tokenize(" ".join([
                    s.name, s.description,
                    *s.domain_tags, *s.capability_tags,
                    s.body[:200],
                ]))
                for s in self._agent_skills
            ]
            self._agent_index = _BM25Index(corpus)
        return self._agent_index

    def _ensure_meta_index(self) -> _BM25Index:
        if self._meta_index is None:
            corpus = [
                _tokenize(" ".join([
                    s.name, s.description,
                    *s.domain_tags, *s.capability_tags,
                    s.body[:200],
                ]))
                for s in self._meta_skills
            ]
            self._meta_index = _BM25Index(corpus)
        return self._meta_index

    def search_meta_skills(
        self, query: str, top_k: int = 5
    ) -> List[Tuple[LoadedSkill, float]]:
        index = self._ensure_meta_index()
        results = index.search(query, top_k=top_k)
        return [(self._meta_skills[i], score) for i, score in results if score > 0]

    def search_agent_skills(
        self, query: str, top_k: int = 10
    ) -> List[Tuple[LoadedSkill, float]]:
        index = self._ensure_agent_index()
        results = index.search(query, top_k=top_k)
        return [(self._agent_skills[i], score) for i, score in results if score > 0]
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_skill_library.py -v`
Expected: all 12 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/agentcoop/skills/library.py src/agentcoop/skills/__init__.py tests/test_skill_library.py
git commit -m "[feat] add SkillLibrary with BM25 search over agent-skills and meta-skills

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Create `SkillBootstrapper` and CLI

**Files:**
- Create: `src/agentcoop/skills/bootstrapper.py`
- Create: `src/agentcoop/bootstrap_cli.py`
- Modify: `pyproject.toml` — add new entrypoint
- Test: `tests/test_skill_library.py` (append)

- [ ] **Step 1: Write tests for SkillBootstrapper**

Append to `tests/test_skill_library.py`:

```python
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
    # Pick the first generated meta-skill and parse it
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skill_library.py::test_bootstrapper_generates_meta_skills -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `src/agentcoop/skills/bootstrapper.py`**

```python
# src/agentcoop/skills/bootstrapper.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from agentcoop.ir.schema import BudgetSpec, ModelSpec, NodeKind, TaskSpec
from agentcoop.workflows.design import BlueprintCompilerConfig, WorkflowPattern
from agentcoop.workflows.patterns import PatternBuildContext, build_pattern_blueprint

JsonDict = Dict[str, Any]

_PATTERN_TAGS: Dict[str, Dict[str, List[str]]] = {
    WorkflowPattern.direct_answer.value: {
        "domain_tags": ["qa", "knowledge", "reasoning"],
        "capability_tags": ["direct_answer", "choice", "review"],
    },
    WorkflowPattern.reason_execute_select.value: {
        "domain_tags": ["math", "symbolic_reasoning"],
        "capability_tags": ["reasoning", "tool_execution", "verification", "selection"],
    },
    WorkflowPattern.code_generate_test_repair.value: {
        "domain_tags": ["coding", "program_synthesis"],
        "capability_tags": ["code_generation", "testing", "repair", "execution"],
    },
    WorkflowPattern.repo_transfer_validate.value: {
        "domain_tags": ["scientific_transfer", "repo_transfer"],
        "capability_tags": ["repo_search", "artifact_validation", "transfer"],
    },
    WorkflowPattern.closed_loop_design_validate.value: {
        "domain_tags": ["panel_design", "design_optimization"],
        "capability_tags": ["closed_loop", "optimization", "validation"],
    },
    WorkflowPattern.specialist_assembly.value: {
        "domain_tags": ["specialist_collaboration", "multi_agent_science"],
        "capability_tags": ["specialist", "collaboration", "integration"],
    },
}


class SkillBootstrapper:
    """Generates meta-skill SKILL.md files from existing workflow patterns."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)

    def bootstrap_all(self) -> List[Path]:
        generated: List[Path] = []
        dummy_ctx = PatternBuildContext(
            task=TaskSpec(task_id="probe", title="probe", description="probe"),
            budget=BudgetSpec(),
            meta={},
            model=ModelSpec(provider="openai", name="gpt-4o-mini"),
            review_model=ModelSpec(provider="openai", name="gpt-4o-mini"),
            design=BlueprintCompilerConfig(),
            resolved_config={},
        )

        for pattern_id, tags in _PATTERN_TAGS.items():
            try:
                blueprint = build_pattern_blueprint(pattern_id, dummy_ctx)
            except Exception:
                continue

            roles = []
            for node in blueprint.all_nodes():
                if node.kind not in {NodeKind.agent, NodeKind.evaluator, NodeKind.router}:
                    continue
                role_tags = list(tags["capability_tags"][:2])
                if node.kind == NodeKind.evaluator:
                    role_tags.append("evaluation")
                elif node.kind == NodeKind.router:
                    role_tags.append("routing")
                roles.append({
                    "role": node.role,
                    "description": node.description or f"{node.role} in {pattern_id}",
                    "skill_tags": role_tags,
                    "kind": node.kind.value,
                })

            edges = []
            for edge in blueprint.base_edges:
                edges.append({"src": edge.src, "dst": edge.dst})

            meta_skill = {
                "name": pattern_id,
                "type": "meta-skill",
                "description": f"Workflow topology from {pattern_id} pattern",
                "domain_tags": tags["domain_tags"],
                "capability_tags": tags["capability_tags"],
                "roles": roles,
                "edges": edges,
            }

            skill_dir = self.output_dir / pattern_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "SKILL.md"
            frontmatter = yaml.dump(meta_skill, default_flow_style=False, sort_keys=False)
            body = f"Auto-generated meta-skill from the {pattern_id} workflow pattern."
            skill_path.write_text(f"---\n{frontmatter}---\n\n{body}\n", encoding="utf-8")
            generated.append(skill_path)

        return generated
```

- [ ] **Step 4: Create `src/agentcoop/bootstrap_cli.py`**

```python
# src/agentcoop/bootstrap_cli.py
"""CLI entrypoint for agentcoop-bootstrap-skills."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentcoop.skills.bootstrapper import SkillBootstrapper


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap meta-skills from existing workflow patterns.")
    parser.add_argument(
        "--output-dir",
        default="skills/meta-skills",
        help="Directory to write generated meta-skill SKILL.md files (default: skills/meta-skills)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    bootstrapper = SkillBootstrapper(output_dir=output_dir)
    generated = bootstrapper.bootstrap_all()
    for path in generated:
        print(f"Generated: {path}")
    print(f"\n{len(generated)} meta-skills generated in {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Add CLI entrypoint to `pyproject.toml`**

Add to `[project.scripts]`:
```toml
agentcoop-bootstrap-skills = "agentcoop.bootstrap_cli:main"
```

- [ ] **Step 6: Reinstall and run tests**

```bash
pip install -e ".[dev]"
pytest tests/test_skill_library.py -v
```
Expected: all 14 tests pass

- [ ] **Step 7: Commit**

```bash
git add src/agentcoop/skills/bootstrapper.py src/agentcoop/bootstrap_cli.py pyproject.toml tests/test_skill_library.py
git commit -m "[feat] add SkillBootstrapper and agentcoop-bootstrap-skills CLI

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Create `SkillDrivenAssembler`

**Files:**
- Create: `src/agentcoop/skills/assembler.py`
- Test: `tests/test_skill_library.py` (append)

- [ ] **Step 1: Write tests for SkillDrivenAssembler**

Append to `tests/test_skill_library.py`:

```python
import textwrap
from agentcoop.skills.assembler import SkillDrivenAssembler
from agentcoop.skills.library import SkillLibrary
from agentcoop.ir.schema import ModelSpec, TaskSpec, BudgetSpec, NodeKind


def test_assembler_builds_blueprint_from_meta_and_agent_skills(tmp_path):
    # Create a meta-skill
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
    # Create agent-skills
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
    # Worker should have work-skill assigned
    worker = next(n for n in blueprint.base_nodes if n.node_id == "worker")
    assert len(worker.skills) >= 1
    assert worker.kind == NodeKind.agent
    # Checker should have check-skill assigned
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
            description: First
            skill_tags: [generic, work]
            kind: agent
          - role: B
            description: Second
            skill_tags: [generic, work]
            kind: agent
        edges:
          - src: A
            dst: B
        ---
    """)
    _write_skill(tmp_path, "only-skill", """
        ---
        name: only-skill
        type: agent-skill
        description: The only skill
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
    # only-skill should be assigned to role A (first in order), not B
    node_a = next(n for n in blueprint.base_nodes if n.node_id == "a")
    node_b = next(n for n in blueprint.base_nodes if n.node_id == "b")
    a_skill_names = [s.name for s in node_a.skills]
    b_skill_names = [s.name for s in node_b.skills]
    assert "only-skill" in a_skill_names
    assert "only-skill" not in b_skill_names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skill_library.py::test_assembler_builds_blueprint_from_meta_and_agent_skills -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `src/agentcoop/skills/assembler.py`**

```python
# src/agentcoop/skills/assembler.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from agentcoop.ir.schema import (
    BudgetSpec,
    EdgeSpec,
    IOContract,
    ModelSpec,
    NodeKind,
    NodeSpec,
    SkillRef,
    TaskSpec,
    WorkflowBlueprint,
)
from agentcoop.runtime.skills import LoadedSkill
from agentcoop.skills.library import SkillLibrary

JsonDict = Dict[str, Any]


class SkillDrivenAssembler:
    """Assembles a WorkflowBlueprint from a meta-skill + per-role agent-skill search."""

    def __init__(
        self,
        library: SkillLibrary,
        max_skills_per_agent: int = 3,
        deduplicate: bool = True,
    ) -> None:
        self._library = library
        self._max_skills = max_skills_per_agent
        self._deduplicate = deduplicate

    def assemble(
        self,
        *,
        meta_skill: LoadedSkill,
        task: TaskSpec,
        budget: BudgetSpec,
        model: ModelSpec,
        review_model: ModelSpec,
    ) -> WorkflowBlueprint:
        assigned_skills: Set[str] = set()
        nodes: List[NodeSpec] = []
        edges: List[EdgeSpec] = []

        for role_spec in meta_skill.roles:
            role_name = str(role_spec.get("role", ""))
            role_desc = str(role_spec.get("description", ""))
            skill_tags = role_spec.get("skill_tags", [])
            kind_str = str(role_spec.get("kind", "agent"))
            kind = NodeKind(kind_str) if kind_str in {k.value for k in NodeKind} else NodeKind.agent

            node_id = role_name.lower().replace(" ", "_")

            # Search agent-skills for this role
            query = " ".join(
                [*[str(t) for t in skill_tags], role_desc, task.description]
            )
            candidates = self._library.search_agent_skills(query, top_k=self._max_skills * 2)

            # Filter and deduplicate
            selected_skills: List[LoadedSkill] = []
            for skill, score in candidates:
                if len(selected_skills) >= self._max_skills:
                    break
                if self._deduplicate and not skill.shareable and skill.name in assigned_skills:
                    continue
                selected_skills.append(skill)
                if not skill.shareable:
                    assigned_skills.add(skill.name)

            # Build SkillRef bindings
            skill_refs = [
                SkillRef(name=s.name, optional=True)
                for s in selected_skills
            ]

            node_model = review_model if kind == NodeKind.evaluator else model

            nodes.append(NodeSpec(
                node_id=node_id,
                kind=kind,
                role=role_name,
                description=role_desc,
                model=node_model,
                system_prompt=meta_skill.body.strip() or None,
                skills=skill_refs,
                io=IOContract(),
            ))

        for edge_spec in meta_skill.edges:
            src = str(edge_spec.get("src", "")).lower().replace(" ", "_")
            dst = str(edge_spec.get("dst", "")).lower().replace(" ", "_")
            edges.append(EdgeSpec(
                edge_id=f"{src}_to_{dst}",
                src=src,
                dst=dst,
            ))

        return WorkflowBlueprint(
            task=task,
            budget=budget,
            base_nodes=nodes,
            base_edges=edges,
            meta={"skill_driven": True, "meta_skill": meta_skill.name},
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_skill_library.py -v`
Expected: all 16 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/agentcoop/skills/assembler.py tests/test_skill_library.py
git commit -m "[feat] add SkillDrivenAssembler for per-agent skill selection

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Add `SkillDrivenSpec` and integrate into `WorkflowCompiler`

**Files:**
- Modify: `src/agentcoop/workflows/design.py`
- Modify: `src/agentcoop/workflows/compiler.py`
- Test: `tests/test_skill_library.py` (append)

- [ ] **Step 1: Write integration test**

Append to `tests/test_skill_library.py`:

```python
from agentcoop.workflows.compiler import WorkflowCompiler
from agentcoop.workflows.design import BlueprintCompilerConfig, TaskProfile, SkillDrivenSpec


def test_compiler_skill_driven_path(tmp_path):
    # Create meta-skill and agent-skills
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
            min_meta_skill_score=0.01,  # low threshold for test
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skill_library.py::test_compiler_skill_driven_path -v`
Expected: FAIL — `SkillDrivenSpec` not defined

- [ ] **Step 3: Add `SkillDrivenSpec` to `design.py`**

In `src/agentcoop/workflows/design.py`, add after `MonitoringSpec`:

```python
class SkillDrivenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    min_meta_skill_score: float = Field(default=0.4, ge=0, le=1)
    max_skills_per_agent: int = Field(default=3, ge=1, le=10)
    deduplicate_skills: bool = True
    skill_search_paths: List[str] = Field(default_factory=list)
```

Add to `BlueprintCompilerConfig`:
```python
class BlueprintCompilerConfig(BaseModel):
    ...
    skill_driven: SkillDrivenSpec = Field(default_factory=SkillDrivenSpec)
```

- [ ] **Step 4: Add `_try_skill_driven` to `compiler.py`**

In `src/agentcoop/workflows/compiler.py`, add imports:

```python
from agentcoop.skills.library import SkillLibrary
from agentcoop.skills.assembler import SkillDrivenAssembler
```

Add method to `WorkflowCompiler`:

```python
def _try_skill_driven(
    self,
    task: TaskSpec,
    budget: BudgetSpec,
    meta: Mapping[str, Any],
    model: ModelSpec,
    review_model: ModelSpec,
    resolved_config: Mapping[str, Any],
) -> tuple[WorkflowBlueprint, JsonDict] | None:
    search_paths = [Path(p) for p in self.config.skill_driven.skill_search_paths]
    if not search_paths:
        return None

    library = SkillLibrary(search_paths=search_paths)
    if not library.meta_skills:
        return None

    query = " ".join([
        task.description,
        self.config.task_profile.domain,
        self.config.task_profile.answer_mode,
        *([t for t in [
            "tool" if self.config.task_profile.requires_tool_execution else "",
            "code" if self.config.task_profile.answer_mode == "code" else "",
            "repo" if self.config.task_profile.requires_repo_search else "",
            "closed_loop" if self.config.task_profile.requires_closed_loop else "",
        ] if t]),
    ])
    meta_results = library.search_meta_skills(query, top_k=3)
    if not meta_results or meta_results[0][1] < self.config.skill_driven.min_meta_skill_score:
        return None

    best_meta_skill = meta_results[0][0]
    assembler = SkillDrivenAssembler(
        library=library,
        max_skills_per_agent=self.config.skill_driven.max_skills_per_agent,
        deduplicate=self.config.skill_driven.deduplicate_skills,
    )
    blueprint = assembler.assemble(
        meta_skill=best_meta_skill,
        task=task,
        budget=budget,
        model=model,
        review_model=review_model,
    )
    self._attach_default_skills(blueprint)

    compile_trace: JsonDict = {
        "enabled": True,
        "mode": "skill-driven",
        "selection_mode": "skill-driven",
        "meta_skill": best_meta_skill.name,
        "meta_skill_score": round(meta_results[0][1], 4),
        "task_profile": self.config.task_profile.model_dump(),
        "agent_skill_assignments": {
            node.node_id: [s.name for s in node.skills]
            for node in blueprint.base_nodes
        },
    }
    blueprint.meta.update({"compile_trace": compile_trace})
    return blueprint, compile_trace
```

Update `compile()` method to call `_try_skill_driven` before existing path:

```python
def compile(self, *, task, budget, meta, model, review_model, resolved_config):
    review_model = review_model or model

    # NEW: skill-driven compilation (first priority)
    if self.config.skill_driven.enabled:
        result = self._try_skill_driven(task, budget, meta, model, review_model, resolved_config)
        if result is not None:
            return result

    # EXISTING: pattern search → synthesis → fallback
    candidates = self.search_candidates(self.config.task_profile)
    ...
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_skill_library.py -v
pytest tests/ -q 2>&1 | tail -10
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/agentcoop/workflows/design.py \
        src/agentcoop/workflows/compiler.py \
        tests/test_skill_library.py
git commit -m "[feat] integrate skill-driven compilation into WorkflowCompiler

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Bootstrap meta-skills and update docs

**Files:**
- Create: `skills/meta-skills/*/SKILL.md` (generated)
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Run the bootstrapper**

```bash
agentcoop-bootstrap-skills --output-dir skills/meta-skills
```

Or if CLI not installed yet:
```bash
python -m agentcoop.bootstrap_cli --output-dir skills/meta-skills
```

Verify files generated:
```bash
ls skills/meta-skills/*/SKILL.md
```

- [ ] **Step 2: Verify bootstrapped skills are searchable**

```bash
python -c "
from pathlib import Path
from agentcoop.skills.library import SkillLibrary
lib = SkillLibrary(search_paths=[Path('skills')])
print(f'Agent skills: {len(lib.agent_skills)}')
print(f'Meta skills: {len(lib.meta_skills)}')
results = lib.search_meta_skills('math reasoning')
for skill, score in results[:3]:
    print(f'  {skill.name}: {score:.3f}')
"
```

- [ ] **Step 3: Update `README.md`**

Add a section after "Benchmark setup" documenting the skill-driven compilation:

```markdown
## Skill-driven compilation

AgentCo-Op supports skill-driven workflow compilation where SKILL.md files define both topology (meta-skills) and agent behavior (agent-skills).

Bootstrap meta-skills from existing patterns:
\`\`\`bash
agentcoop-bootstrap-skills
\`\`\`

Enable skill-driven compilation in experiment config:
\`\`\`yaml
workflow_design:
  enabled: true
  skill_driven:
    enabled: true
    skill_search_paths: ["./skills"]
\`\`\`
```

- [ ] **Step 4: Update `CLAUDE.md`**

Add skill-driven compilation to the architecture section and key modules table.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -q 2>&1 | tail -10
```

- [ ] **Step 6: Commit**

```bash
git add skills/meta-skills/ README.md CLAUDE.md
git commit -m "[docs] bootstrap meta-skills, update docs for AgentCo-Op

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: Push**

```bash
git push origin main
```
