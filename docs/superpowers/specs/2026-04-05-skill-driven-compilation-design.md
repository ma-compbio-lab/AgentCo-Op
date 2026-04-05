# AgentCo-Op: Skill-Driven Workflow Compilation — Design Spec

## Scope

Two sub-projects:
- **Sub-project A**: Full rename `agentcoop` → `agentcoop` (package, imports, CLI, configs, docs)
- **Sub-project B**: Skill-driven workflow compilation with two-tier skill system

## Constraints

- Build on existing infrastructure (synthesis backend, BM25 searcher, SkillRegistry)
- No LLM calls during compilation — purely BM25 search + template assembly
- Backward compatible: existing patterns become auto-bootstrapped meta-skills
- Existing SKILL.md files without `type` field treated as `agent-skill`
- Retrieval: BM25 only (extend existing `ComponentSearcher` approach)
- External skill sources: git-cloned to local cache, indexed alongside internal skills

---

## Sub-project A: Rename agentcoop → agentcoop

### Scope

| What | Before | After |
|---|---|---|
| Python package | `src/agentcoop/` | `src/agentcoop/` |
| All imports | `from agentcoop.X` | `from agentcoop.X` |
| CLI: workflow runner | `agentcoop-run` | `agentcoop-run` |
| CLI: experiments | `agentcoop-exp` | `agentcoop-exp` |
| CLI: new | — | `agentcoop-bootstrap-skills` |
| Config dir | `src/agentcoop/conf/` | `src/agentcoop/conf/` |
| Env var | `DYNAFORGE_SKILL_PATHS` | `AGENTCOOP_SKILL_PATHS` |
| Artifacts | `.agentcoop_artifacts` | `.agentcoop_artifacts` |
| pyproject.toml name | `agentcoop` | `agentcoop` |

Done as a single dedicated commit before any feature work.

---

## Sub-project B: Skill-Driven Workflow Compilation

### 1. SKILL.md Format Extension

Two tiers, both using existing SKILL.md format with extended frontmatter:

#### Agent-skills (existing format, extended)

```yaml
---
name: spatial-data-analyst
type: agent-skill
description: Analyze spatial transcriptomics data with scanpy/squidpy
domain_tags: [spatial, transcriptomics, bioinformatics]
capability_tags: [data_analysis, visualization, gene_expression]
allowed_tools: ["code-sandbox:*"]
shareable: false
---
<behavioral instructions for the agent>
```

New frontmatter fields:
- `type`: `agent-skill` (default if omitted, backward compatible)
- `domain_tags`: list of domain keywords for BM25 indexing
- `capability_tags`: list of capability keywords for BM25 indexing
- `shareable`: boolean, default `false`. If `true`, skill can be assigned to multiple roles in the same workflow.

#### Meta-skills (new — define topology templates)

```yaml
---
name: spatial-pipeline
type: meta-skill
description: End-to-end spatial transcriptomics analysis pipeline
domain_tags: [spatial, transcriptomics]
capability_tags: [pipeline, multi_step, closed_loop]
roles:
  - role: DataLoader
    description: Load and preprocess spatial data
    skill_tags: [data_loading, preprocessing, spatial]
    kind: agent
  - role: Analyzer
    description: Run spatial analysis methods
    skill_tags: [spatial, analysis, statistics]
    kind: agent
  - role: Validator
    description: Validate results and generate figures
    skill_tags: [validation, visualization]
    kind: evaluator
edges:
  - src: DataLoader
    dst: Analyzer
  - src: Analyzer
    dst: Validator
---
<optional high-level pipeline description injected as global context to all agents>
```

Meta-skill frontmatter fields:
- `type`: `meta-skill` (required for topology-defining skills)
- `roles`: list of agent role definitions, each with:
  - `role`: string, the role name (becomes `NodeSpec.role`)
  - `description`: string (becomes `NodeSpec.description`)
  - `skill_tags`: list of strings used for BM25 agent-skill search for this role
  - `kind`: NodeKind string, default `agent` (one of: `agent`, `evaluator`, `router`)
- `edges`: list of `{src, dst}` pairs referencing role names. During assembly, role names are lowercased to become `NodeSpec.node_id` values (e.g., role `DataLoader` → node_id `dataloader`).
- `domain_tags`, `capability_tags`: same as agent-skills, used for meta-skill search

### 2. Skill Library

#### Directory structure

```
skills/
  agent-skills/
    structured-json-discipline/SKILL.md     # existing (no type field = agent-skill)
    spatial-data-analyst/SKILL.md           # hand-authored
    code-executor/SKILL.md                  # hand-authored
    ...
  meta-skills/
    direct-answer/SKILL.md                  # bootstrapped from pattern
    reason-execute-select/SKILL.md          # bootstrapped from pattern
    code-generate-test-repair/SKILL.md      # bootstrapped from pattern
    repo-transfer-validate/SKILL.md         # bootstrapped from pattern
    closed-loop-design-validate/SKILL.md    # bootstrapped from pattern
    specialist-assembly/SKILL.md            # bootstrapped from pattern
    spatial-pipeline/SKILL.md               # hand-authored
    ...
```

#### Skill sources (config)

```yaml
skill_sources:
  - path: ./skills                          # local project skills (default)
  - path: ~/.agentcoop/skills               # user-level skills
  - url: https://github.com/user/skills     # git-cloned on first use
```

External `url` sources:
- Cloned to `~/.agentcoop/skill_cache/<repo-hash>/` on first access
- Re-pulled on explicit `agentcoop-bootstrap-skills --sync` command
- Indexed alongside internal skills in the unified BM25 index

#### Bootstrapping from patterns

CLI command: `agentcoop-bootstrap-skills`

For each existing pattern builder (direct_answer, reason_execute_select, etc.):
1. Build blueprint with a dummy context (same as `ComponentLibrary._build()`)
2. Extract LLM nodes → roles list with descriptions
3. Extract edges
4. Derive `domain_tags` and `capability_tags` from `_score_pattern` logic in compiler.py
5. Write `skills/meta-skills/{pattern-id}/SKILL.md`

This is a one-time codegen step, not runtime. Output is committed to the repo.

#### `SkillLibrary` class

New class in `agentcoop/skills/library.py`. Extends the indexing concept from `SkillRegistry`:

```python
class SkillLibrary:
    def __init__(self, search_paths: list[Path], cache_dir: Path):
        self._agent_skills: list[LoadedSkill] = []
        self._meta_skills: list[LoadedSkill] = []
        self._bm25_agent: BM25Index     # lazy
        self._bm25_meta: BM25Index      # lazy

    def search_meta_skills(self, profile: TaskProfile) -> list[tuple[LoadedSkill, float]]:
        """BM25 search over meta-skills using task profile."""

    def search_agent_skills(
        self, skill_tags: list[str], role_description: str, task_description: str
    ) -> list[tuple[LoadedSkill, float]]:
        """BM25 search over agent-skills using role context."""
```

- Scans all search paths for SKILL.md files
- Partitions into agent-skills and meta-skills by `type` frontmatter field
- Builds separate BM25 indices (meta-skills are typically small; agent-skills can be large)
- BM25 corpus per skill: `name + description + domain_tags + capability_tags + body_summary`

### 3. Compilation Pipeline

Five stages, integrated into `WorkflowCompiler.compile()`:

```
Task arrives
  → Stage 1: BM25 search meta-skills (query: task description + profile tags)
      → Ranked meta-skill candidates
  → Stage 2: Select best meta-skill (top-1 if score ≥ threshold)
      → Meta-skill defines: roles[], edges[], global context body
  → Stage 3: For each role, BM25 search agent-skills
      → Query: role.skill_tags + role.description + task.description
      → Deduplicate: each non-shareable skill assigned to highest-scoring role only
      → Top-K per role (default K=3, configurable via skill_driven.max_skills_per_agent)
  → Stage 4: Assemble WorkflowBlueprint
      → Each role → NodeSpec (kind from meta-skill, model from config)
      → Each role's agent-skills → node.skills as SkillRef list
      → Meta-skill edges → EdgeSpec list
      → Meta-skill body → blueprint.meta["workflow_context"]
  → Stage 5: Validate via BlueprintValidator
  → Fallback: If no meta-skill ≥ threshold → existing pattern selector + synthesis
```

#### Compiler integration

```python
class WorkflowCompiler:
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

- Skill-driven path runs before existing path
- No LLM call — purely BM25 search + template assembly
- Deterministic and fast

#### Config extension

New `SkillDrivenSpec` in `design.py`:

```python
class SkillDrivenSpec(BaseModel):
    enabled: bool = False
    min_meta_skill_score: float = Field(default=0.4, ge=0, le=1)
    max_skills_per_agent: int = Field(default=3, ge=1, le=10)
    deduplicate_skills: bool = True
```

Added to `BlueprintCompilerConfig`:
```python
class BlueprintCompilerConfig(BaseModel):
    ...
    skill_driven: SkillDrivenSpec = Field(default_factory=SkillDrivenSpec)
```

### 4. Per-Agent Skill Selection

#### Selection algorithm per role

1. Build BM25 query: `role.skill_tags + role.description + task.description`
2. Search agent-skill library → ranked results
3. Deduplicate: if `deduplicate_skills=True`, exclude skills already assigned to higher-scoring roles (unless `shareable: true`)
4. Take top-K (K = `max_skills_per_agent`)
5. Bind each as `SkillRef(name=skill.name, prompt_mode="full")`

#### Example: spatial pipeline

| Role | BM25 query (from skill_tags) | Assigned agent-skills |
|---|---|---|
| DataLoader | `data_loading preprocessing spatial` | `scanpy-data-loader`, `anndata-handler` |
| Analyzer | `spatial analysis statistics` | `spatial-data-analyst`, `squidpy-methods` |
| Validator | `validation visualization` | `figure-validator`, `structured-json-discipline` |

Each agent gets different skills → different system prompts → different tool allowlists → specialized behavior.

#### Tool policy

Each agent's tool allowlist = intersection of its assigned skills' `allowed_tools` (existing `SkillPromptBundle.apply_tool_policy` behavior, no change needed).

#### Skill deduplication

- Default: each non-shareable agent-skill assigned to at most one role
- `structured-json-discipline` has `shareable: true` → available to all roles
- Deduplication order: roles processed in meta-skill declaration order; higher-listed roles get first pick

### 5. New Files

| File | Responsibility |
|---|---|
| `agentcoop/skills/library.py` | `SkillLibrary` — unified index over agent-skills + meta-skills, BM25 search |
| `agentcoop/skills/bootstrapper.py` | `SkillBootstrapper` — generates meta-skill SKILL.md from existing patterns |
| `agentcoop/skills/assembler.py` | `SkillDrivenAssembler` — Stage 3-4: agent-skill search + blueprint assembly |
| `agentcoop/workflows/design.py` | Extended with `SkillDrivenSpec` |
| `agentcoop/workflows/compiler.py` | Extended with `_try_skill_driven()` |
| `agentcoop/bootstrap_cli.py` | `agentcoop-bootstrap-skills` entrypoint |
| `skills/meta-skills/*/SKILL.md` | Bootstrapped meta-skill files (one per pattern) |

### 6. Commit Organization

| Commit | Scope |
|---|---|
| `[refactor] rename agentcoop to agentcoop` | Sub-project A |
| `[feat] extend SKILL.md format with type, tags, roles, edges` | Schema + parsing |
| `[feat] add SkillLibrary with BM25 search over agent-skills and meta-skills` | Library + indexing |
| `[feat] add SkillBootstrapper and agentcoop-bootstrap-skills CLI` | Pattern → meta-skill codegen |
| `[feat] add SkillDrivenAssembler for per-agent skill selection` | Stage 3-4 of pipeline |
| `[feat] integrate skill-driven compilation into WorkflowCompiler` | Stage 1-2 + fallback |
| `[docs] update README and CLAUDE.md for AgentCo-Op` | Documentation |

---

## References

- [Agent Skills for LLMs: Architecture, Acquisition, Security](https://arxiv.org/html/2602.12430v3)
- [SkillFlow: Scalable and Efficient Agent Skill Retrieval](https://arxiv.org/html/2504.06188v2)
- [SoK: Agentic Skills — Beyond Tool Use in LLM Agents](https://arxiv.org/html/2602.20867v1)
- [SkillRL: Evolving Agents via Recursive Skill-Augmented RL](https://arxiv.org/html/2602.08234v1)
- [When Single-Agent with Skills Replace Multi-Agent Systems](https://arxiv.org/html/2601.04748v1)
