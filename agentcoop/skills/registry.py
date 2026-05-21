"""Skill registry.

Loads meta-skills (Markdown-with-YAML-frontmatter) and agent-skills (YAML)
from disk. Both kinds are exposed as typed Pydantic models and can be
queried by tag / domain / keyword.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml

from agentcoop.core.schema import AgentSkill, MetaSkill


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def parse_skill_markdown(text: str) -> tuple[dict, str]:
    """Split a Markdown-with-YAML-frontmatter document.

    Returns (frontmatter_dict, body_markdown).
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("skill file missing YAML frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        raise ValueError("skill frontmatter must be a mapping")
    return fm, m.group(2)


def _body_sections(body: str) -> dict[str, str]:
    """Split a markdown body into `# Heading → text` sections."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("# "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[2:].strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _as_list(x) -> list[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v).strip() for v in x if str(v).strip()]
    if isinstance(x, str):
        return [line.strip("- \t") for line in x.splitlines() if line.strip("- \t")]
    return []


def load_meta_skill(path: Path) -> MetaSkill:
    text = path.read_text(encoding="utf-8")
    fm, body = parse_skill_markdown(text)
    sections = _body_sections(body)
    data = dict(fm)
    data.setdefault("intent", sections.get("Intent", ""))
    if "when_to_use" not in data and "When to use" in sections:
        data["when_to_use"] = _as_list(sections["When to use"])
    if "when_not_to_use" not in data and "When not to use" in sections:
        data["when_not_to_use"] = _as_list(sections["When not to use"])
    data["source_path"] = str(path)
    # Pydantic will drop unknown keys per `extra='allow'`.
    return MetaSkill.model_validate(data)


def load_agent_skill(path: Path) -> AgentSkill:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: agent skill must be a YAML mapping")
    data["source_path"] = str(path)
    return AgentSkill.model_validate(data)


def load_agent_skills(path: Path) -> list[AgentSkill]:
    """Load one or more `AgentSkill`s from a YAML file.

    Accepts two layouts so a single file can declare a family of related
    skills (e.g. `configs/skills/math_skills.yaml`) without forcing a
    one-file-per-skill split:

      single:  {name: ..., kind: agent_skill, ...}
      bundle:  {skills: [{name: ..., ...}, {name: ..., ...}]}
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: agent skill file must be a YAML mapping")
    items = data.get("skills") if "skills" in data and "name" not in data else [data]
    if not isinstance(items, list):
        raise ValueError(f"{path}: 'skills' must be a list")
    skills: list[AgentSkill] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: each skill entry must be a mapping")
        entry = dict(entry)
        entry.setdefault("source_path", str(path))
        skills.append(AgentSkill.model_validate(entry))
    return skills


class SkillRegistry:
    def __init__(self) -> None:
        self.meta: dict[str, MetaSkill] = {}
        self.agents: dict[str, AgentSkill] = {}

    # -- loading --------------------------------------------------------------

    def load_dir(self, root: str | Path) -> "SkillRegistry":
        """Load skill cards from a directory tree.

        Two layouts are recognized:
          * `root/meta/*.md` + `root/agents/*.yaml` — the canonical
            framework layout (used by `agentcoop/skills/`).
          * `root/*.yaml` — a flat directory of agent-skill cards
            (used by `configs/skills/`). Both single-skill and
            `{skills: [...]}` bundle layouts are accepted.
        """
        root = Path(root)
        meta_dir = root / "meta"
        agents_dir = root / "agents"
        loaded_structured = False
        if meta_dir.is_dir():
            loaded_structured = True
            for p in sorted(meta_dir.glob("*.md")):
                skill = load_meta_skill(p)
                self.meta[skill.name] = skill
        if agents_dir.is_dir():
            loaded_structured = True
            for p in sorted(agents_dir.glob("*.yaml")):
                for skill in load_agent_skills(p):
                    self.agents[skill.name] = skill
        if not loaded_structured and root.is_dir():
            for p in sorted(root.glob("*.yaml")):
                for skill in load_agent_skills(p):
                    self.agents[skill.name] = skill
        return self

    # -- retrieval ------------------------------------------------------------

    def search_meta(self, profile, top_k: int = 8) -> list[MetaSkill]:
        """Rank meta-skills by overlap with the profile.

        v0: keyword + tag scoring. An embedding-based retriever can be
        plugged in by overriding this method.
        """
        task_text = f"{profile.raw_task} {' '.join(profile.domain)} {profile.answer_type}".lower()
        scored: list[tuple[float, MetaSkill]] = []
        for skill in self.meta.values():
            score = _score_meta(skill, profile, task_text)
            scored.append((score, skill))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return [s for _, s in scored[:top_k]]

    def search_agents(self, profile, top_k: int = 20) -> list[AgentSkill]:
        task_text = f"{profile.raw_task} {' '.join(profile.domain)} {profile.answer_type}".lower()
        scored: list[tuple[float, AgentSkill]] = []
        for skill in self.agents.values():
            score = _score_agent(skill, profile, task_text)
            scored.append((score, skill))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return [s for _, s in scored[:top_k]]


# -------------------- scoring helpers ---------------------------------------


def _score_meta(skill: MetaSkill, profile, task_text: str) -> float:
    signals = skill.task_signals or {}
    score = 0.0

    domains = set(map(str, signals.get("domains") or [])) | set(skill.tags)
    if domains & set(profile.domain):
        score += 2.5
    if any(d.lower() in task_text for d in domains):
        score += 0.5

    answer_types = set(map(str, signals.get("answer_type") or []))
    if profile.answer_type in answer_types:
        score += 1.5

    verif = set(map(str, signals.get("verification_available") or []))
    if profile.verification_available in verif:
        score += 1.0

    # Soft continuous signals
    if signals.get("requires_retrieval") and profile.retrieval_need >= 0.5:
        score += 1.0
    if signals.get("requires_tools") and profile.tool_need >= 0.5:
        score += 0.5
    if signals.get("requires_repo") and profile.repo_execution_need >= 0.5:
        score += 1.0

    score -= 0.3 * max(0, skill.complexity_level - 2)  # gentle simplicity bias
    return score


def _score_agent(skill: AgentSkill, profile, task_text: str) -> float:
    score = 0.0
    tags = set(skill.applicable_tags) | set(skill.capabilities)
    if tags & set(profile.domain):
        score += 2.0
    for t in tags:
        if t.lower() in task_text:
            score += 0.5
    if skill.backend_type == "sandbox_repo" and profile.repo_execution_need >= 0.5:
        score += 1.5
    if skill.risk_level == "high" and profile.risk_level == "low":
        score -= 1.0
    return score


def iter_skill_files(root: str | Path) -> Iterable[Path]:
    root = Path(root)
    yield from sorted((root / "meta").glob("*.md"))
    yield from sorted((root / "agents").glob("*.yaml"))


__all__ = [
    "SkillRegistry",
    "load_meta_skill",
    "load_agent_skill",
    "load_agent_skills",
    "parse_skill_markdown",
]
