"""Environment manager.

Auto-installs the Python packages each external-agent wrapper declares
as required, so the user does NOT have to run `pip install ...` before
`agentcoop collaborate`. Records every install action (or
already-present detection) in a JSON manifest the orchestrator persists
under `manifests/env_manifest.json`.

Modes:
- `--no-docker` (default for CS1 right now): run `pip install` in the
  current Python interpreter.
- `--docker` (when the daemon is up): the SandboxBuilder already weaves
  these requirements into each Dockerfile via `extra_pip_packages`, so
  the EnvManager's job in Docker mode is just to write the manifest.

This module is **additive**: nothing in agentcoop today imports it.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class PackageRequirement(BaseModel):
    """One declared package + the import name to probe for it.

    Many packages ship under a different import name than their PyPI
    name (e.g. `pip install scikit-learn` → `import sklearn`). When
    `import_name` is omitted we use the package name with `-` → `_`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    import_name: str | None = None
    version_spec: str | None = None  # e.g. ">=1.0,<2", "==0.12.11"

    @property
    def pip_arg(self) -> str:
        return f"{self.name}{self.version_spec or ''}"

    @property
    def probe_name(self) -> str:
        return self.import_name or self.name.replace("-", "_")


class PackageStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    probe_name: str
    requested: str
    installed_version: str | None = None
    state: str  # "already_present" | "installed" | "install_failed" | "skipped_docker"
    elapsed_s: float = 0.0
    error: str | None = None


class EnvReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    python_executable: str
    python_version: str
    started_at: str
    completed_at: str
    elapsed_s: float
    packages: list[PackageStatus] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Wrapper registry
#
# Each external-agent wrapper module declares its requirements at import
# time via `register_required_packages("AgentName", [...])`. The orchestrator
# resolves them per agent before invoking the adapter.
# ---------------------------------------------------------------------------


_REQUIREMENTS: dict[str, list[PackageRequirement]] = {}


def register_required_packages(
    agent_name: str,
    packages: Sequence[str | PackageRequirement | tuple[str, str | None]],
) -> None:
    """Register the Python packages needed by `agent_name`'s adapter.

    Accepted entries:
    - bare PyPI name string (e.g. `"anndata"`)
    - `(pypi_name, import_name_or_none)` tuple (e.g. `("scikit-learn", "sklearn")`)
    - a `PackageRequirement` instance
    """
    parsed: list[PackageRequirement] = []
    for item in packages:
        if isinstance(item, PackageRequirement):
            parsed.append(item)
        elif isinstance(item, tuple):
            name, import_name = (item + (None,))[:2]
            parsed.append(PackageRequirement(name=name, import_name=import_name))
        elif isinstance(item, str):
            # support `name>=1.0` strings
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*([<>=!~^].*)?$", item.strip())
            if not m:
                raise ValueError(f"unparseable requirement: {item!r}")
            parsed.append(PackageRequirement(name=m.group(1), version_spec=m.group(2)))
        else:
            raise TypeError(f"unsupported requirement entry: {item!r}")
    _REQUIREMENTS.setdefault(agent_name.lower(), []).extend(parsed)


def required_packages_for(agent_name: str) -> list[PackageRequirement]:
    return list(_REQUIREMENTS.get(agent_name.lower(), []))


def all_required_packages() -> dict[str, list[PackageRequirement]]:
    return {k: list(v) for k, v in _REQUIREMENTS.items()}


# ---------------------------------------------------------------------------
# EnvManager — main API
# ---------------------------------------------------------------------------


@dataclass
class EnvManager:
    """Resolve declared requirements; install missing ones; write manifest."""

    mode: str = "local"  # "local" | "docker"
    pip_args: tuple[str, ...] = ("install", "--no-input", "--disable-pip-version-check")
    timeout_s: int = 600
    log: list[str] = field(default_factory=list)

    # ---- introspection -----------------------------------------------------

    def is_present(self, req: PackageRequirement) -> str | None:
        """Return the installed version string when present, else None."""
        spec = importlib.util.find_spec(req.probe_name)
        if spec is None:
            return None
        try:
            return importlib.metadata.version(req.name)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    # ---- ensure ------------------------------------------------------------

    def ensure_one(self, req: PackageRequirement) -> PackageStatus:
        t0 = time.monotonic()
        if self.mode == "docker":
            return PackageStatus(
                name=req.name,
                probe_name=req.probe_name,
                requested=req.pip_arg,
                installed_version=None,
                state="skipped_docker",
                elapsed_s=0.0,
            )
        version = self.is_present(req)
        if version is not None:
            self.log.append(f"[env] already_present: {req.name}=={version}")
            return PackageStatus(
                name=req.name,
                probe_name=req.probe_name,
                requested=req.pip_arg,
                installed_version=version,
                state="already_present",
                elapsed_s=time.monotonic() - t0,
            )
        self.log.append(f"[env] installing: {req.pip_arg}")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", *self.pip_args, "--quiet", req.pip_arg],
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-1500:]
            self.log.append(f"[env] install_failed: {req.pip_arg}: {err[:200]}")
            return PackageStatus(
                name=req.name,
                probe_name=req.probe_name,
                requested=req.pip_arg,
                installed_version=None,
                state="install_failed",
                elapsed_s=time.monotonic() - t0,
                error=err,
            )
        # importlib caches negative results; clear so the new package is visible.
        importlib.invalidate_caches()
        version = self.is_present(req)
        self.log.append(f"[env] installed: {req.name}=={version or '?'}")
        return PackageStatus(
            name=req.name,
            probe_name=req.probe_name,
            requested=req.pip_arg,
            installed_version=version,
            state="installed",
            elapsed_s=time.monotonic() - t0,
        )

    def ensure(self, requirements: Iterable[PackageRequirement]) -> list[PackageStatus]:
        return [self.ensure_one(r) for r in requirements]

    # ---- manifest ----------------------------------------------------------

    def write_manifest(
        self,
        path: str | Path,
        statuses: Sequence[PackageStatus],
        *,
        elapsed_s: float | None = None,
    ) -> Path:
        report = EnvReport(
            mode=self.mode,
            python_executable=sys.executable,
            python_version=".".join(map(str, sys.version_info[:3])),
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            elapsed_s=float(elapsed_s or sum(s.elapsed_s for s in statuses)),
            packages=list(statuses),
        )
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return p


# ---------------------------------------------------------------------------
# Higher-level convenience used by the orchestrator
# ---------------------------------------------------------------------------


def ensure_env_for_agents(
    agent_names: Iterable[str],
    *,
    workdir: str | Path,
    mode: str = "local",
) -> EnvReport:
    """Resolve every declared requirement for the given agents and persist
    the result under `<workdir>/manifests/env_manifest.json`.
    """
    mgr = EnvManager(mode=mode)
    statuses: list[PackageStatus] = []
    seen: set[str] = set()
    for name in agent_names:
        for req in required_packages_for(name):
            key = req.name.lower()
            if key in seen:
                continue
            seen.add(key)
            statuses.append(mgr.ensure_one(req))
    manifest_path = Path(workdir) / "manifests" / "env_manifest.json"
    mgr.write_manifest(manifest_path, statuses)
    return EnvReport(
        mode=mgr.mode,
        python_executable=sys.executable,
        python_version=".".join(map(str, sys.version_info[:3])),
        started_at="",
        completed_at="",
        elapsed_s=sum(s.elapsed_s for s in statuses),
        packages=statuses,
    )


__all__ = [
    "PackageRequirement",
    "PackageStatus",
    "EnvReport",
    "EnvManager",
    "register_required_packages",
    "required_packages_for",
    "all_required_packages",
    "ensure_env_for_agents",
]
