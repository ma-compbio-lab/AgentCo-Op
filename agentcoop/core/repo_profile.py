"""Repository profiler.

Given a GitHub URL, clone (or read from cache) and inspect the repository to
emit a structured `RepoProfile` that downstream modules (`SandboxBuilder`,
`AgentCard`) consume. Designed for the `external_repo_collaboration`
meta-skill (Case Study 1) but generic across repo pairs.

Detection strategy is deliberately conservative — we look for canonical
filenames and parse only when the file is small. Any LLM-driven inference
happens elsewhere (the planner can read the profile and refine it).

This module is **additive**: it does not modify any existing AgentCo-Op
behaviour. Other modules opt in by importing it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


WrapperStrategyT = Literal[
    "headless_python",
    "headless_python_or_notebook",
    "fastapi_service",
    "cli_subprocess",
    "library_import",
    "notebook_papermill",
]


class RepoProfile(BaseModel):
    """Structured snapshot of an external GitHub repository.

    The shape is consciously aligned with the JSON example in
    `docs/experiments/case_study_1.md` §4.1 so users can dump the profile to disk and
    diff it without translation.
    """

    model_config = ConfigDict(extra="allow")

    repo_name: str
    repo_url: str
    local_path: str | None = None
    commit_sha: str | None = None

    detected_language: list[str] = Field(default_factory=list)
    environment_files: list[str] = Field(default_factory=list)
    dependency_files: dict[str, str] = Field(default_factory=dict)
    run_modes: list[str] = Field(default_factory=list)
    candidate_capabilities: list[str] = Field(default_factory=list)
    entry_scripts: list[str] = Field(default_factory=list)

    requires_api_key: bool = False
    api_key_envs: list[str] = Field(default_factory=list)
    preferred_wrapper_strategy: WrapperStrategyT = "headless_python"

    package_managers: list[str] = Field(default_factory=list)
    python_version_hint: str | None = None
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cloning
# ---------------------------------------------------------------------------


@dataclass
class CloneResult:
    path: Path
    commit_sha: str | None
    cached: bool


def clone_repo(
    url: str,
    *,
    dest_root: str | Path = "external",
    name: str | None = None,
    shallow: bool = True,
    use_cache: bool = True,
    timeout_s: int = 180,
) -> CloneResult:
    """Best-effort `git clone` into `dest_root/<name>`.

    Falls back to using an existing cached clone when `use_cache=True` and the
    destination already exists. Returns the absolute path + the resolved
    commit SHA. Raises `RuntimeError` if the clone fails and no cache exists.
    """
    name = name or _infer_repo_name(url)
    dest_root = Path(dest_root).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    target = dest_root / name

    if target.exists() and use_cache:
        sha = _resolve_head(target)
        return CloneResult(path=target, commit_sha=sha, cached=True)

    args = ["git", "clone"]
    if shallow:
        args += ["--depth", "1"]
    args += ["--recurse-submodules", url, str(target)]
    try:
        proc = subprocess.run(
            args,
            check=True,
            timeout=timeout_s,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if target.exists():
            sha = _resolve_head(target)
            return CloneResult(path=target, commit_sha=sha, cached=True)
        raise RuntimeError(
            f"git clone {url} failed: {getattr(exc, 'stderr', '') or str(exc)}"
        ) from exc
    return CloneResult(path=target, commit_sha=_resolve_head(target), cached=False)


def _infer_repo_name(url: str) -> str:
    return re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1])


def _resolve_head(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------


_ENV_FILES = (
    "environment.yml", "environment.yaml", "conda-environment.yml",
    "pyproject.toml", "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock",
    "setup.py", "setup.cfg",
    "requirements.txt", "requirements-dev.txt", "requirements-cpu.txt",
    "flake.nix", "shell.nix", "default.nix",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile",
)

_README_NAMES = ("README.md", "README.rst", "README.txt", "README")

_SCRIPT_HINTS = (
    "main.py", "run.py", "app.py", "server.py", "worker.py", "cli.py",
    "main_cascade.py", "main_summary.py", "agent.py", "predict.py", "infer.py",
)

_API_KEY_PATTERNS = (
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"ANTHROPIC_API_KEY", re.IGNORECASE),
    re.compile(r"AZURE_OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"openai\.api_key", re.IGNORECASE),
    re.compile(r"openai\.AzureOpenAI", re.IGNORECASE),
)

_LANG_BY_EXT = {
    ".py": "Python",
    ".ipynb": "Python",
    ".r": "R",
    ".jl": "Julia",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".cpp": "C++",
    ".c": "C",
    ".java": "Java",
    ".sh": "Shell",
    ".nix": "Nix",
}


def profile_repo(
    repo_url: str,
    *,
    repo_name: str | None = None,
    local_path: str | Path | None = None,
    role_hint: str | None = None,
    max_files_scanned: int = 600,
) -> RepoProfile:
    """Inspect a cloned (or to-be-cloned) repository and return a profile.

    If `local_path` is provided, the repo is read from disk and not re-cloned.
    Otherwise the function performs a shallow clone via `clone_repo()`.
    """
    if local_path is not None:
        path = Path(local_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"local_path does not exist: {path}")
        sha = _resolve_head(path)
    else:
        clone = clone_repo(repo_url, name=repo_name)
        path, sha = clone.path, clone.commit_sha

    name = repo_name or _infer_repo_name(repo_url)

    env_files: list[str] = []
    dep_files: dict[str, str] = {}
    for cand in _ENV_FILES:
        p = path / cand
        if p.is_file():
            env_files.append(cand)
            try:
                if p.stat().st_size <= 256_000:
                    dep_files[cand] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    languages = _detect_languages(path, max_files=max_files_scanned)
    package_managers = _detect_package_managers(env_files)
    python_version = _detect_python_version(dep_files)
    api_key, api_envs = _detect_api_key_usage(path, max_files=max_files_scanned)
    run_modes = _infer_run_modes(env_files, dep_files, path)
    entry_scripts = _detect_entry_scripts(path)
    capabilities = _infer_capabilities(name, role_hint, dep_files, path)
    strategy = _choose_wrapper_strategy(env_files, dep_files, run_modes)

    return RepoProfile(
        repo_name=name,
        repo_url=repo_url,
        local_path=str(path),
        commit_sha=sha,
        detected_language=languages,
        environment_files=env_files,
        dependency_files=dep_files,
        run_modes=run_modes,
        candidate_capabilities=capabilities,
        entry_scripts=entry_scripts,
        requires_api_key=api_key,
        api_key_envs=api_envs,
        preferred_wrapper_strategy=strategy,
        package_managers=package_managers,
        python_version_hint=python_version,
    )


# --- helpers ----------------------------------------------------------------


def _detect_languages(path: Path, *, max_files: int) -> list[str]:
    counts: dict[str, int] = {}
    for i, p in enumerate(_walk_files(path, max_files)):
        ext = p.suffix.lower()
        lang = _LANG_BY_EXT.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    # Order by frequency, keep top 4.
    return [lang for lang, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:4]]


def _walk_files(path: Path, limit: int) -> Iterable[Path]:
    n = 0
    for root, dirs, files in os.walk(path):
        # Skip vendored / VCS / build dirs.
        dirs[:] = [
            d for d in dirs
            if d not in {".git", ".venv", "venv", "node_modules", "build",
                          "dist", "__pycache__", ".mypy_cache", ".pytest_cache"}
        ]
        for f in files:
            n += 1
            if n > limit:
                return
            yield Path(root) / f


def _detect_package_managers(env_files: list[str]) -> list[str]:
    mapping = {
        "environment.yml": "conda",
        "environment.yaml": "conda",
        "conda-environment.yml": "conda",
        "pyproject.toml": "pep621",
        "uv.lock": "uv",
        "poetry.lock": "poetry",
        "Pipfile": "pipenv",
        "Pipfile.lock": "pipenv",
        "setup.py": "setuptools",
        "setup.cfg": "setuptools",
        "requirements.txt": "pip",
        "requirements-dev.txt": "pip",
        "requirements-cpu.txt": "pip",
        "flake.nix": "nix",
        "shell.nix": "nix",
        "default.nix": "nix",
    }
    seen: list[str] = []
    for f in env_files:
        m = mapping.get(f)
        if m and m not in seen:
            seen.append(m)
    return seen


_PY_VER_RE = re.compile(r"python\s*[><=!~^]+\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def _detect_python_version(dep_files: dict[str, str]) -> str | None:
    for content in dep_files.values():
        m = _PY_VER_RE.search(content)
        if m:
            return m.group(1)
    return None


def _detect_api_key_usage(path: Path, *, max_files: int) -> tuple[bool, list[str]]:
    envs: set[str] = set()
    found = False
    n = 0
    for p in _walk_files(path, max_files):
        if p.suffix not in {".py", ".md", ".yaml", ".yml", ".toml", ".env"}:
            continue
        n += 1
        if n > max_files:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in _API_KEY_PATTERNS:
            m = pat.search(text)
            if m:
                found = True
                token = m.group(0).upper()
                if "OPENAI" in token:
                    envs.add("OPENAI_API_KEY")
                elif "ANTHROPIC" in token:
                    envs.add("ANTHROPIC_API_KEY")
                elif "AZURE" in token:
                    envs.add("AZURE_OPENAI_API_KEY")
    return found, sorted(envs)


def _infer_run_modes(
    env_files: list[str],
    dep_files: dict[str, str],
    path: Path,
) -> list[str]:
    modes: list[str] = []
    if "Dockerfile" in env_files:
        modes.append("docker_native")
    if any(f.endswith(".ipynb") for f in os.listdir(path) if (path / f).is_file()):
        modes.append("notebook")
    # README clues
    for r in _README_NAMES:
        rp = path / r
        if not rp.is_file():
            continue
        try:
            txt = rp.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            continue
        if "fastapi" in txt or "uvicorn" in txt:
            modes.append("fastapi_backend")
        if "streamlit" in txt:
            modes.append("streamlit_ui")
        if "papermill" in txt:
            modes.append("notebook_papermill")
        if "headless" in txt or "cli" in txt:
            modes.append("headless")
    if not modes:
        modes.append("headless")
    # Dedup, preserve order.
    seen: list[str] = []
    for m in modes:
        if m not in seen:
            seen.append(m)
    return seen


def _detect_entry_scripts(path: Path) -> list[str]:
    found: list[str] = []
    for hint in _SCRIPT_HINTS:
        # Walk up to depth 3 to find canonical entry scripts.
        for cand in path.rglob(hint):
            rel = cand.relative_to(path).as_posix()
            depth = rel.count("/")
            if depth <= 3 and rel not in found:
                found.append(rel)
                if len(found) >= 12:
                    return found
    return found


_CAPABILITY_KEYWORDS = {
    "spatial transcriptomics workflow orchestration": ("spatial", "merfish", "anndata", "scanpy", "communities"),
    "differential expression analysis": ("differential expression", "rank_genes_groups", "deseq", "edger", "wilcoxon"),
    "gene set analysis": ("gene set", "gsea", "enrichment", "geneagent", "gene-set"),
    "cell-type annotation": ("cell type", "leiden", "celltypist", "annotation"),
    "image analysis": ("image", "imaging", "microscopy", "tissue"),
    "code generation": ("code generation", "programmer", "codegen"),
    "biological knowledge retrieval": ("ncbi", "ensembl", "uniprot", "geneontology", "pubmed"),
    "self-verification": ("self-verify", "self verification", "verification"),
    "hypothesis synthesis": ("hypothesis", "report", "summary"),
    "agent orchestration": ("planner", "recruiter", "manager", "evaluator", "reporter"),
}


def _infer_capabilities(
    name: str,
    role_hint: str | None,
    dep_files: dict[str, str],
    path: Path,
) -> list[str]:
    blob = " ".join(dep_files.values()).lower()
    for r in _README_NAMES:
        rp = path / r
        if rp.is_file():
            try:
                blob += "\n" + rp.read_text(encoding="utf-8", errors="replace").lower()[:60_000]
            except Exception:
                pass
    name_l = name.lower()
    if role_hint:
        blob += "\n" + role_hint.lower()
    caps: list[str] = []
    for cap, kws in _CAPABILITY_KEYWORDS.items():
        if any(kw in blob or kw in name_l for kw in kws):
            caps.append(cap)
    return caps


def _choose_wrapper_strategy(
    env_files: list[str],
    dep_files: dict[str, str],
    run_modes: list[str],
) -> WrapperStrategyT:
    if "fastapi_backend" in run_modes:
        return "fastapi_service"
    if "notebook_papermill" in run_modes or "notebook" in run_modes:
        return "headless_python_or_notebook"
    if "Dockerfile" in env_files:
        return "headless_python"
    return "headless_python"


__all__ = [
    "RepoProfile",
    "CloneResult",
    "clone_repo",
    "profile_repo",
]
