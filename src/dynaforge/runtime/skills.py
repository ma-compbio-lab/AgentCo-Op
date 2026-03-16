from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Sequence

import yaml

from dynaforge.ir.schema import NodeSpec, SkillPromptMode, SkillRef

if TYPE_CHECKING:
    from dynaforge.integrations.tool_registry import ToolCandidate

JsonDict = Dict[str, Any]

DEFAULT_SKILL_PATH_ENV = "DYNAFORGE_SKILL_PATHS"


class SkillRegistryError(RuntimeError):
    """Raised when a configured skill cannot be resolved or parsed."""


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    path: str
    description: str
    body: str
    metadata: JsonDict

    @property
    def allowed_tools(self) -> list[str]:
        raw = self.metadata.get("allowed-tools", self.metadata.get("allowed_tools", []))
        if isinstance(raw, str):
            value = raw.strip()
            return [value] if value else []
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return []


@dataclass
class SkillPromptBundle:
    prompt_text: str = ""
    applied: list[JsonDict] = field(default_factory=list)
    missing_optional: list[JsonDict] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    allowed_tool_groups: list[list[str]] = field(default_factory=list)

    def has_trace(self) -> bool:
        return bool(self.applied or self.missing_optional or self.missing_required)

    def trace_payload(self) -> JsonDict:
        return {
            "applied": list(self.applied),
            "missing_optional": list(self.missing_optional),
            "missing_required": list(self.missing_required),
            "tool_policy": self.tool_policy_trace(),
        }

    def tool_policy_enabled(self) -> bool:
        return bool(self.allowed_tool_groups)

    def tool_policy_trace(self) -> JsonDict:
        return {
            "enabled": self.tool_policy_enabled(),
            "mode": "intersection-allowlist" if self.allowed_tool_groups else "none",
            "allowed_tool_groups": [list(group) for group in self.allowed_tool_groups],
        }

    def apply_tool_policy(
        self,
        candidates: Sequence["ToolCandidate"],
    ) -> tuple[list["ToolCandidate"], JsonDict]:
        if not self.allowed_tool_groups:
            return list(candidates), {
                **self.tool_policy_trace(),
                "pre_filter_candidate_count": len(candidates),
                "post_filter_candidate_count": len(candidates),
                "blocked_candidates": [],
            }

        allowed: list["ToolCandidate"] = []
        blocked: list[JsonDict] = []
        for candidate in candidates:
            if self._candidate_allowed(candidate):
                allowed.append(candidate)
                continue
            blocked.append(
                {
                    "server": candidate.ref.server,
                    "tool": candidate.ref.tool,
                    "source": candidate.source,
                }
            )
        return allowed, {
            **self.tool_policy_trace(),
            "pre_filter_candidate_count": len(candidates),
            "post_filter_candidate_count": len(allowed),
            "blocked_candidates": blocked,
        }

    def _candidate_allowed(self, candidate: "ToolCandidate") -> bool:
        return all(
            any(self._pattern_matches(candidate, pattern) for pattern in group)
            for group in self.allowed_tool_groups
        )

    @staticmethod
    def _pattern_matches(candidate: "ToolCandidate", pattern: str) -> bool:
        normalized = pattern.strip()
        if not normalized or normalized in {"*", "any"}:
            return True
        server = candidate.ref.server
        tool = candidate.ref.tool
        qualified = f"{server}:{tool}"
        if normalized == qualified or normalized == tool or normalized == server:
            return True
        if normalized.endswith(":*"):
            return server == normalized[:-2]
        return False


class SkillRegistry:
    """Resolves node-level skill bindings from named or path-based SKILL.md files."""

    def __init__(
        self,
        search_paths: Optional[Sequence[str | os.PathLike[str]]] = None,
        *,
        project_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root or os.getcwd()).expanduser().resolve()
        self.search_paths = self._normalize_search_paths(search_paths)
        self._loaded_by_path: dict[Path, LoadedSkill] = {}
        self._paths_by_name: dict[str, list[Path]] = {}
        self._indexed = False

    def render_prompt_for_node(self, node: NodeSpec) -> SkillPromptBundle:
        if not node.skills:
            return SkillPromptBundle()

        blocks: list[str] = []
        applied: list[JsonDict] = []
        missing_optional: list[JsonDict] = []
        missing_required: list[str] = []
        allowed_tool_groups: list[list[str]] = []

        for binding in node.skills:
            try:
                skill = self.resolve(binding)
            except SkillRegistryError as exc:
                if binding.optional:
                    missing_optional.append(
                        {
                            "name": binding.name,
                            "path": binding.path,
                            "error": str(exc),
                        }
                    )
                    continue
                missing_required.append(str(exc))
                continue

            blocks.append(self._render_skill_block(skill, binding))
            applied.append(
                {
                    "name": skill.name,
                    "path": skill.path,
                    "description": skill.description,
                    "prompt_mode": binding.prompt_mode.value,
                    "notes": binding.notes,
                    "arguments": dict(binding.arguments),
                    "max_chars": binding.max_chars,
                    "allowed_tools": skill.allowed_tools,
                }
            )
            if skill.allowed_tools:
                allowed_tool_groups.append(skill.allowed_tools)

        prompt_text = ""
        if blocks:
            prompt_text = (
                "Reusable skills are active for this node. Follow them unless they conflict with the explicit task, "
                "schema, or tool constraints.\n\n"
                + "\n\n".join(blocks)
            )

        return SkillPromptBundle(
            prompt_text=prompt_text,
            applied=applied,
            missing_optional=missing_optional,
            missing_required=missing_required,
            allowed_tool_groups=allowed_tool_groups,
        )

    def resolve(self, binding: SkillRef) -> LoadedSkill:
        if binding.path:
            return self._load_skill(self._resolve_binding_path(binding.path))

        if not binding.name:
            raise SkillRegistryError("Skill binding is missing both name and path")

        key = self._normalize_name(binding.name)
        self._ensure_index()
        candidates = self._paths_by_name.get(key, [])
        if not candidates:
            raise SkillRegistryError(
                f"Skill '{binding.name}' was not found in search paths: {', '.join(str(path) for path in self.search_paths)}"
            )
        return self._load_skill(candidates[0])

    def _normalize_search_paths(
        self,
        search_paths: Optional[Sequence[str | os.PathLike[str]]],
    ) -> list[Path]:
        package_skill_root = Path(__file__).resolve().parent.parent / "skills"
        env_paths = [
            segment.strip()
            for segment in os.environ.get(DEFAULT_SKILL_PATH_ENV, "").split(os.pathsep)
            if segment.strip()
        ]
        raw_paths: list[str | os.PathLike[str]] = [
            *(search_paths or []),
            *env_paths,
            self.project_root / "skills",
            package_skill_root,
            Path.home() / ".codex" / "skills",
            Path.home() / ".agents" / "skills",
        ]
        normalized: list[Path] = []
        seen: set[Path] = set()
        for raw_path in raw_paths:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = (self.project_root / path).resolve()
            else:
                path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            normalized.append(path)
        return normalized

    def _ensure_index(self) -> None:
        if self._indexed:
            return
        for root in self.search_paths:
            if not root.exists():
                continue
            for skill_path in self._iter_skill_files(root):
                try:
                    skill = self._load_skill(skill_path)
                except SkillRegistryError:
                    continue
                key = self._normalize_name(skill.name)
                existing = self._paths_by_name.setdefault(key, [])
                if skill_path not in existing:
                    existing.append(skill_path)
        self._indexed = True

    @staticmethod
    def _iter_skill_files(root: Path) -> Iterable[Path]:
        if root.is_file():
            if root.name == "SKILL.md":
                yield root
            return
        yield from root.rglob("SKILL.md")

    def _resolve_binding_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.project_root / candidate).resolve()
        if candidate.is_dir():
            candidate = candidate / "SKILL.md"
        if not candidate.exists():
            raise SkillRegistryError(f"Skill path does not exist: {candidate}")
        if candidate.name != "SKILL.md":
            raise SkillRegistryError(f"Skill path must point to a SKILL.md file or a containing directory: {candidate}")
        return candidate

    def _load_skill(self, path: Path) -> LoadedSkill:
        cached = self._loaded_by_path.get(path)
        if cached is not None:
            return cached

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillRegistryError(f"Failed to read skill file {path}: {exc}") from exc

        metadata_text, body = self._split_frontmatter(raw_text)
        metadata: JsonDict = {}
        if metadata_text:
            try:
                parsed = yaml.safe_load(metadata_text)
            except yaml.YAMLError as exc:
                raise SkillRegistryError(f"Failed to parse YAML frontmatter in {path}: {exc}") from exc
            if parsed is None:
                metadata = {}
            elif isinstance(parsed, Mapping):
                metadata = dict(parsed)
            else:
                raise SkillRegistryError(f"Skill frontmatter must be a mapping in {path}")

        name = str(metadata.get("name") or path.parent.name).strip()
        if not name:
            raise SkillRegistryError(f"Skill file {path} does not declare a usable name")
        description = str(metadata.get("description", "")).strip()
        skill = LoadedSkill(
            name=name,
            path=str(path),
            description=description,
            body=body.strip(),
            metadata=metadata,
        )
        self._loaded_by_path[path] = skill
        return skill

    @staticmethod
    def _split_frontmatter(raw_text: str) -> tuple[str, str]:
        if not raw_text.startswith("---\n"):
            return "", raw_text
        end_marker = "\n---\n"
        end_index = raw_text.find(end_marker, 4)
        if end_index == -1:
            return "", raw_text
        return raw_text[4:end_index], raw_text[end_index + len(end_marker) :]

    @staticmethod
    def _normalize_name(name: str) -> str:
        return name.strip().casefold()

    @staticmethod
    def _summary_text(skill: LoadedSkill) -> str:
        if skill.description:
            return skill.description
        paragraphs = [paragraph.strip() for paragraph in skill.body.split("\n\n") if paragraph.strip()]
        return paragraphs[0] if paragraphs else ""

    def _render_skill_block(self, skill: LoadedSkill, binding: SkillRef) -> str:
        if binding.prompt_mode == SkillPromptMode.summary:
            instructions = self._summary_text(skill)
        else:
            instructions = skill.body or self._summary_text(skill)

        if binding.max_chars is not None and len(instructions) > binding.max_chars:
            instructions = instructions[: binding.max_chars].rstrip() + "\n[truncated]"

        lines = [f"Skill: {skill.name}"]
        if skill.description:
            lines.append(f"Description: {skill.description}")
        if binding.notes:
            lines.append(f"Node-specific notes: {binding.notes}")
        if binding.arguments:
            lines.append(f"Skill arguments: {binding.arguments}")
        if skill.allowed_tools:
            lines.append("Allowed tools referenced by skill metadata: " + ", ".join(skill.allowed_tools))
        lines.append("Instructions:")
        lines.append(instructions or "(No body text provided by the skill file.)")
        return "\n".join(lines)
