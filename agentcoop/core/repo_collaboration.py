"""External-repo-collaboration orchestration.

Loads a request YAML matching the schema in `docs/experiments/case_study_1.md` §3.3,
profiles each repository, generates Dockerfiles via the
`SandboxBuilder`, registers an `AgentCard` per repo, runs each agent's
adapter (Docker or local-python `--no-docker`), brokers typed handoffs,
and writes a `run_manifest.json` + `compiled_workflow_graph.json` plus
all per-step artifacts.

The orchestration is **generic** — Tissue × Gene is just the inaugural
example. Anything specific to those two repos lives in
`agentcoop/wrappers/tissueagent/` and `agentcoop/wrappers/geneagent/`.

This module is **additive**: it does not import or modify any
runtime/compiler/gates code that backs the existing benchmarks.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

from agentcoop.core.agent_card import AgentCard, AgentRegistry
from agentcoop.core.artifact_broker import ArtifactBroker, HandoffResult
from agentcoop.core.collab_report import (
    write_collaboration_log,
    write_final_report,
)
from agentcoop.core.env_manager import (
    EnvReport,
    ensure_env_for_agents,
    required_packages_for,
)
from agentcoop.core.repo_profile import RepoProfile, profile_repo
from agentcoop.core.sandbox_build import (
    SandboxBuilder,
    SandboxSpec,
    _docker_available,
    spec_from_profile,
)
from agentcoop.core.topology_viz import render_topology


# Default tag for the generic Python runtime image (built from
# docker/agentcoop-runtime.Dockerfile). Adapters that need a non-Python
# toolchain — e.g. R/Bioconductor for Seurat & Signac — override this by
# setting `runtime_image` on the request's per-repo `sandbox_overrides`.
DEFAULT_RUNTIME_IMAGE = "agentcoop-runtime:case-study"
RUNTIME_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "agentcoop-runtime.Dockerfile"


# ---------------------------------------------------------------------------
# Request schema (loose dict; we don't force a Pydantic shape so users can
# extend the YAML without us shipping a new release)
# ---------------------------------------------------------------------------


@dataclass
class CollaborationRequest:
    case_id: str
    repositories: list[dict[str, Any]] = field(default_factory=list)
    dataset: dict[str, Any] = field(default_factory=dict)
    task: dict[str, Any] = field(default_factory=dict)
    required_outputs: list[str] = field(default_factory=list)
    success_targets: dict[str, Any] = field(default_factory=dict)
    mode: str = "autonomous_repo_wrapping"
    max_replans: int = 2
    raw: dict[str, Any] = field(default_factory=dict)
    # Topology selector. Two patterns are supported today:
    #   - "linear_handoff" (default; CS1):
    #       repos[0] (upstream) → broker → repos[1] (downstream) → integrator
    #   - "parallel_then_join" (CS2):
    #       repos[0..N-1] run in parallel; an optional `join_agent` consumes
    #       all upstream responses + extra `join_inputs`; then the integrator
    #       wraps the join result.
    topology: str = "linear_handoff"
    join_agent: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CollaborationRequest":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(
            case_id=str(data.get("case_id", "ad_hoc")),
            repositories=list(data.get("repositories", [])),
            dataset=dict(data.get("dataset", {}) or {}),
            task=dict(data.get("task", {}) or {}),
            required_outputs=list(data.get("required_outputs", []) or []),
            success_targets=dict(data.get("success_targets", {}) or {}),
            mode=str(data.get("mode", "autonomous_repo_wrapping")),
            max_replans=int(data.get("max_replans", 2)),
            topology=str(data.get("topology", "linear_handoff")),
            join_agent=dict(data.get("join_agent", {}) or {}),
            parameters=dict(data.get("parameters", {}) or {}),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Adapter registry — maps an AgentCard.name to a Python callable that takes
# a request dict and returns a response dict. New repos can register here
# without modifying the orchestration code.
# ---------------------------------------------------------------------------


AdapterFn = Callable[[dict[str, Any]], dict[str, Any]]
_ADAPTERS: dict[str, AdapterFn] = {}


def register_local_adapter(name: str) -> Callable[[AdapterFn], AdapterFn]:
    """Decorator: register a local-Python adapter for a given agent name."""

    def deco(fn: AdapterFn) -> AdapterFn:
        _ADAPTERS[name.lower()] = fn
        return fn

    return deco


def get_local_adapter(name: str) -> AdapterFn | None:
    return _ADAPTERS.get(name.lower())


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class CollaborationResult:
    case_id: str
    workdir: Path
    registry: AgentRegistry
    repo_profiles: dict[str, RepoProfile]
    handoffs: list[HandoffResult]
    artifacts: dict[str, str]
    notes: list[str]
    status: str  # "success" | "partial" | "synthetic_fallback" | "failed"


class RepoCollaborationOrchestrator:
    """End-to-end runner for a generic two-repo collaboration."""

    def __init__(
        self,
        request: CollaborationRequest,
        *,
        workdir: str | Path,
        no_docker: bool = False,
        external_root: str | Path = "external",
    ) -> None:
        self.request = request
        self.workdir = Path(workdir).expanduser().resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.no_docker = no_docker
        self.external_root = Path(external_root).expanduser().resolve()
        self.external_root.mkdir(parents=True, exist_ok=True)
        self.builder = SandboxBuilder(self.workdir)
        self.broker = ArtifactBroker(audit_dir=self.workdir / "manifests")
        self.registry = AgentRegistry()
        self.profiles: dict[str, RepoProfile] = {}
        self.notes: list[str] = []

    # ---- Phase 1 — profile + sandbox specs --------------------------------

    def _profile_repos(self) -> dict[str, RepoProfile]:
        for repo in self.request.repositories:
            url = repo["url"]
            name = repo.get("name") or _infer_name(url)
            try:
                prof = profile_repo(
                    url,
                    repo_name=name,
                    role_hint=repo.get("role_hint", ""),
                )
            except Exception as exc:
                self.notes.append(f"clone/profile failed for {name}: {exc}")
                # Fall back to a minimal profile so downstream steps can continue.
                prof = RepoProfile(
                    repo_name=name,
                    repo_url=url,
                    notes=[f"profile fallback: {exc}"],
                    warnings=["could not clone or read repo"],
                )
            self.profiles[name] = prof
            (self.workdir / "manifests").mkdir(parents=True, exist_ok=True)
            (self.workdir / "manifests" / f"repo_profile_{name}.json").write_text(
                prof.model_dump_json(indent=2), encoding="utf-8"
            )
        return self.profiles

    def _build_sandboxes(self) -> dict[str, dict[str, Any]]:
        report: dict[str, dict[str, Any]] = {}
        specs: list[SandboxSpec] = []
        # In Docker mode build a single generic Python runtime image
        # once; per-repo R/bioconductor images are built lazily by
        # `_ensure_image()` only when a spec overrides `runtime_image`.
        if not self.no_docker:
            rt = self._build_runtime_image()
            report["__runtime__"] = rt
        for name, prof in self.profiles.items():
            override = next(
                (r.get("sandbox_overrides", {}) or {}
                 for r in self.request.repositories if r.get("name") == name),
                {},
            )
            spec = spec_from_profile(prof, name=name, overrides=override)
            res = self.builder.build(
                spec,
                profile=prof,
                # Docker-mode wires the build path; --no-docker keeps the
                # historical dry-run behaviour (artifacts + spec only).
                dry_run=self.no_docker,
            )
            specs.append(spec)
            report[name] = {
                "image_built": res.image_built,
                "dockerfile": str(res.dockerfile_path),
                "notes": res.notes,
                "runtime_image": _runtime_image_for(spec),
            }
        # Compose for both services together.
        compose_path = self.builder.write_compose(specs)
        report["__compose__"] = str(compose_path)
        (self.workdir / "manifests" / "docker_build_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report

    def _build_runtime_image(self) -> dict[str, Any]:
        """Build (or skip if cached) the generic Python runtime image.

        Returns a small dict the caller drops into the docker-build
        report so the manifest records what got built.
        """
        info: dict[str, Any] = {
            "image": DEFAULT_RUNTIME_IMAGE,
            "dockerfile": str(RUNTIME_DOCKERFILE),
            "built": False,
            "cached": False,
            "notes": [],
        }
        if not _docker_available():
            info["notes"].append("docker daemon not available; skipping runtime image build")
            return info
        if not RUNTIME_DOCKERFILE.is_file():
            info["notes"].append(f"runtime Dockerfile not found at {RUNTIME_DOCKERFILE}")
            return info
        # Check cache.
        try:
            r = subprocess.run(
                ["docker", "image", "inspect", DEFAULT_RUNTIME_IMAGE],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                info["cached"] = True
                info["notes"].append(f"image already present: {DEFAULT_RUNTIME_IMAGE}")
                return info
        except Exception as exc:
            info["notes"].append(f"docker image inspect failed: {exc}")
        repo_root = RUNTIME_DOCKERFILE.parent.parent
        try:
            r = subprocess.run(
                ["docker", "build", "-f", str(RUNTIME_DOCKERFILE),
                 "-t", DEFAULT_RUNTIME_IMAGE, str(repo_root)],
                capture_output=True, text=True, timeout=1800,
            )
            if r.returncode == 0:
                info["built"] = True
                info["notes"].append(f"image built: {DEFAULT_RUNTIME_IMAGE}")
            else:
                tail = (r.stderr or r.stdout or "").splitlines()[-12:]
                info["notes"].append("docker build failed; tail=" + " | ".join(tail))
        except subprocess.TimeoutExpired:
            info["notes"].append("docker build timed out after 30 min")
        return info

    # ---- Phase 2 — register agent cards -----------------------------------

    def _register_cards(self) -> AgentRegistry:
        for repo in self.request.repositories:
            name = repo.get("name") or _infer_name(repo["url"])
            prof = self.profiles[name]
            spec = spec_from_profile(prof, name=name)
            card = AgentCard.from_profile(
                prof,
                role=repo.get("role_hint") or "external sandboxed agent",
                sandbox_spec=spec,
            )
            self.registry.add(card)
        (self.workdir / "agent_registry.json").write_text(
            json.dumps(self.registry.to_json_dict(), indent=2), encoding="utf-8"
        )
        return self.registry

    # ---- Phase 3 — execute adapters in DAG order --------------------------

    def _run_adapter(
        self,
        card_name: str,
        invocation: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the agent's adapter.

        `--no-docker`:    call the in-process Python adapter registered with
                          `register_local_adapter`.
        `--docker`:       `docker run --rm` the runtime image (default
                          `agentcoop-runtime:case-study` or whatever
                          `SandboxSpec.runtime_image` overrides for that
                          agent), bind-mount the host workdir at the
                          identical path so the request JSON's absolute
                          paths resolve unchanged inside the container,
                          then read back the response file written by
                          `python -m agentcoop.wrappers <name> <invoke>`.
        """
        if self.no_docker:
            adapter = get_local_adapter(card_name)
            if adapter is None:
                return {
                    "status": "failed",
                    "summary": f"no local adapter registered for {card_name}",
                    "artifacts": {},
                    "warnings": [f"register_local_adapter('{card_name}') is missing"],
                }
            return adapter(invocation)
        return self._run_adapter_in_docker(card_name, invocation)

    def _image_for(self, card_name: str) -> str:
        """Resolve which Docker image to run for `card_name`.

        Per-repo `sandbox_overrides.runtime_image` wins; falls back to
        `join_agent.runtime_image` for the join card; defaults to the
        generic Python runtime.
        """
        repo_meta = next(
            (r for r in self.request.repositories if r.get("name") == card_name),
            None,
        )
        if repo_meta is not None:
            override = (repo_meta.get("sandbox_overrides") or {}).get("runtime_image")
            return override or DEFAULT_RUNTIME_IMAGE
        join = self.request.join_agent or {}
        if card_name == join.get("name"):
            return join.get("runtime_image") or DEFAULT_RUNTIME_IMAGE
        return DEFAULT_RUNTIME_IMAGE

    def _run_adapter_in_docker(
        self,
        card_name: str,
        invocation: dict[str, Any],
    ) -> dict[str, Any]:
        if not _docker_available():
            return {
                "status": "failed",
                "summary": "docker daemon not reachable",
                "artifacts": {},
                "warnings": ["start colima/docker and rerun with --docker"],
            }
        # Drop the request file under the workdir so it shares the
        # bind-mount with the rest of the run artifacts.
        invoke_dir = self.workdir / "_invoke"
        invoke_dir.mkdir(parents=True, exist_ok=True)
        ts = str(int(time.time() * 1000))
        invoke_path = invoke_dir / f"{card_name}_{ts}.json"
        invoke_path.write_text(json.dumps(invocation, indent=2), encoding="utf-8")
        response_path = invoke_path.with_name(invoke_path.stem + ".response.json")

        image = self._image_for(card_name)

        # Bind-mount the workdir AND the dataset/external roots at the
        # same absolute path so the host paths in `invocation` work
        # inside the container without translation. Also mount HOME so
        # cached datasets under e.g. ~/Documents/... resolve.
        repo_root = Path(__file__).resolve().parents[2]
        mounts = [
            f"-v={repo_root}:{repo_root}",
            f"-v={self.workdir}:{self.workdir}",
        ]
        # Optional data + external roots (skip if outside the repo to
        # minimise the container's view of the host filesystem).
        for extra in (self.external_root, repo_root / "data"):
            try:
                p = Path(extra).resolve()
            except Exception:
                continue
            if p.exists() and str(p).startswith(str(repo_root)):
                mounts.append(f"-v={p}:{p}")
        env_args: list[str] = []
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
            if os.environ.get(var):
                env_args.extend(["-e", f"{var}={os.environ[var]}"])
        # Adapters resolve dataset paths like `data/<name>/...` relative to
        # the repo root (this is how the --no-docker path runs them too).
        # Set the container cwd to repo_root so relative paths resolve
        # identically inside the container.
        cmd = [
            "docker", "run", "--rm",
            "-w", str(repo_root),
            *mounts,
            *env_args,
            image,
            card_name, str(invoke_path),
        ]
        # 2 h timeout: case-study adapters that read 100M-fragment ATAC
        # files (e.g. Signac peak-to-gene on 32 k cells × 344 k peaks)
        # routinely run 60-90 min. Override per-call via the env var
        # `AGENTCOOP_DOCKER_RUN_TIMEOUT_S` if a wrapper is even slower.
        try:
            timeout_s = int(os.environ.get("AGENTCOOP_DOCKER_RUN_TIMEOUT_S", "7200"))
        except ValueError:
            timeout_s = 7200
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {
                "status": "failed",
                "summary": f"docker run timed out (>{timeout_s}s)",
                "artifacts": {},
                "warnings": [" ".join(shlex.quote(c) for c in cmd)],
            }
        # Prefer the response file (always JSON-valid, indented). Fall
        # back to parsing stdout's last line if the adapter crashed
        # before writing the file.
        if response_path.exists():
            try:
                return json.loads(response_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.notes.append(f"docker adapter response parse error: {exc}")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").splitlines()[-15:]
            return {
                "status": "failed",
                "summary": f"docker run rc={r.returncode}",
                "artifacts": {},
                "warnings": tail,
            }
        # Best-effort stdout parse.
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as exc:
            return {
                "status": "failed",
                "summary": f"could not parse adapter response: {exc}",
                "artifacts": {},
                "warnings": [r.stdout[-500:], r.stderr[-500:]],
            }

    # ---- env manager ------------------------------------------------------

    def _ensure_env(self) -> EnvReport | None:
        """Ask EnvManager to pip-install whatever the registered adapters
        declared via `register_required_packages`. In Docker mode this
        is a no-op (the SandboxBuilder handles deps inside images)."""
        names = [r.get("name") or _infer_name(r["url"])
                 for r in self.request.repositories]
        # Include the optional join_agent so its declared deps get the
        # same auto-install treatment as the repo agents.
        join_name = (self.request.join_agent or {}).get("name")
        if join_name and join_name not in names:
            names.append(join_name)
        # Skip silently when no requirements are declared at all — keeps
        # the manifest tidy for repos that ship their own bootstrap.
        any_declared = any(required_packages_for(n) for n in names)
        if not any_declared:
            return None
        mode = "docker" if not self.no_docker else "local"
        try:
            report = ensure_env_for_agents(names, workdir=self.workdir, mode=mode)
        except Exception as exc:
            self.notes.append(f"env_manager: {exc}")
            return None
        # Surface install_failed packages as warnings.
        for pkg in report.packages:
            if pkg.state == "install_failed":
                self.notes.append(
                    f"env_manager: pip install {pkg.requested!r} failed — "
                    f"adapter may degrade. error_tail={(pkg.error or '')[:140]!r}"
                )
        return report

    # ---- topology executors -------------------------------------------------

    def _execute_linear_handoff(self) -> dict[str, Any]:
        """CS1-style upstream → broker → downstream → integrator.

        Returns a dict with `upstream_name`, `downstream_name`,
        `upstream_resp`, `downstream_resp`, `handoffs`, `integration`,
        and an empty `branch_responses` for parity with the parallel
        topology.
        """
        upstream_name, downstream_name = self.registry.names()[:2]

        upstream_dir = self.workdir / "artifacts" / f"{upstream_name.lower()}_run"
        upstream_dir.mkdir(parents=True, exist_ok=True)
        upstream_req = {
            "task_id": f"{self.request.case_id}_{upstream_name.lower()}",
            "capability": "run_primary_analysis",
            "input": {
                "task_description": self.request.task.get("description", ""),
                "task_meta": self.request.task,
                "dataset": self.request.dataset,
                "success_targets": self.request.success_targets,
                "parameters": self.request.parameters,
            },
            "output_dir": str(upstream_dir),
        }
        upstream_resp = self._run_adapter(upstream_name, upstream_req)
        (upstream_dir / "response.json").write_text(
            json.dumps(upstream_resp, indent=2), encoding="utf-8"
        )

        handoffs: list[HandoffResult] = []
        gene_set = (upstream_resp.get("artifacts") or {}).get("gene_set") or []
        marker_csv = (upstream_resp.get("artifacts") or {}).get("marker_genes_csv")
        downstream_input_path = self.workdir / "artifacts" / f"{downstream_name.lower()}_input.json"
        h_genes = self.broker.handoff_gene_set(
            producer=upstream_name,
            consumer=downstream_name,
            genes=gene_set,
            organism=str(self.request.task.get("organism", "human")),
            biological_context=str(self.request.task.get("description", "")),
            contrast_description=str(upstream_resp.get("summary", "")),
            write_to=downstream_input_path,
        )
        handoffs.append(h_genes)
        if marker_csv and Path(marker_csv).exists():
            handoffs.append(
                self.broker.handoff_csv(
                    producer=upstream_name,
                    consumer=downstream_name,
                    path=marker_csv,
                )
            )

        downstream_dir = self.workdir / "artifacts" / f"{downstream_name.lower()}_run"
        downstream_dir.mkdir(parents=True, exist_ok=True)
        downstream_req = {
            "task_id": f"{self.request.case_id}_{downstream_name.lower()}",
            "capability": "interpret_handoff",
            "input": (h_genes.artifact.payload or {}) | {
                "task_description": self.request.task.get("description", ""),
                "biological_context": self.request.task.get("description", ""),
            },
            "output_dir": str(downstream_dir),
        }
        downstream_resp = self._run_adapter(downstream_name, downstream_req)
        (downstream_dir / "response.json").write_text(
            json.dumps(downstream_resp, indent=2), encoding="utf-8"
        )

        integration_dir = self.workdir / "artifacts" / "integration"
        integration_dir.mkdir(parents=True, exist_ok=True)
        integration = self._integrate(
            upstream_name, upstream_resp,
            downstream_name, downstream_resp,
            out_dir=integration_dir,
        )

        return {
            "upstream_name": upstream_name,
            "downstream_name": downstream_name,
            "upstream_resp": upstream_resp,
            "downstream_resp": downstream_resp,
            "handoffs": handoffs,
            "integration": integration,
            "branch_responses": {},
        }

    def _execute_parallel_then_join(self) -> dict[str, Any]:
        """Generic N-way fan-out + join + integrator.

        - Every registered repo runs in parallel (each with its own
          output dir).
        - The `request.join_agent.name` adapter consumes all branch
          responses + extra `join_inputs` (e.g. CellMarker file path),
          and is treated as the conceptual "downstream" for the
          existing finalize / report pipeline.
        - The LLM integrator wraps the join output as in CS1.

        Backwards-compatible: if no `join_agent` is declared the join
        step is skipped and we treat the second registered repo as the
        downstream for reporting parity.
        """
        names = self.registry.names()
        upstream_name = names[0]

        # ---- run every branch in parallel via a thread pool -------------
        from concurrent.futures import ThreadPoolExecutor

        branch_dirs: dict[str, Path] = {}
        branch_requests: dict[str, dict[str, Any]] = {}
        for name in names:
            d = self.workdir / "artifacts" / f"{name.lower()}_run"
            d.mkdir(parents=True, exist_ok=True)
            branch_dirs[name] = d
            branch_requests[name] = {
                "task_id": f"{self.request.case_id}_{name.lower()}",
                "capability": "run_branch_analysis",
                "input": {
                    "task_description": self.request.task.get("description", ""),
                    "task_meta": self.request.task,
                    "dataset": self.request.dataset,
                    "success_targets": self.request.success_targets,
                    "parameters": self.request.parameters,
                    "branch_index": names.index(name),
                    "n_branches": len(names),
                },
                "output_dir": str(d),
            }

        # Branch concurrency: default = max(2, n_branches). Memory-
        # constrained hosts (e.g. CS2 Path C with R Seurat + R Signac
        # each ≥4 GB on a 10 GB colima) can override via the env var
        # `AGENTCOOP_BRANCH_CONCURRENCY` to serialise.
        env_cap = os.environ.get("AGENTCOOP_BRANCH_CONCURRENCY")
        try:
            cap = int(env_cap) if env_cap else max(2, len(names))
        except ValueError:
            cap = max(2, len(names))
        max_workers = max(1, min(cap, len(names)))
        branch_responses: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {
                name: pool.submit(self._run_adapter, name, branch_requests[name])
                for name in names
            }
            for name, fut in futs.items():
                resp = fut.result()
                branch_responses[name] = resp
                (branch_dirs[name] / "response.json").write_text(
                    json.dumps(resp, indent=2), encoding="utf-8"
                )

        # ---- broker handoffs (one per branch's primary marker output) ---
        handoffs: list[HandoffResult] = []
        for name, resp in branch_responses.items():
            arts = resp.get("artifacts") or {}
            for key in ("rna_top_markers_json", "atac_top_marker_genes_json", "marker_genes_csv"):
                p = arts.get(key)
                if p and Path(p).exists():
                    kind = "json_path" if str(p).endswith(".json") else "csv_path"
                    handoffs.append(
                        self.broker.handoff_file(
                            producer=name,
                            consumer=self.request.join_agent.get("name") or "join",
                            path=p,
                            kind=kind,
                        )
                    )

        # ---- join agent --------------------------------------------------
        join_name = (self.request.join_agent or {}).get("name") or ""
        downstream_name = join_name or names[-1]
        downstream_resp: dict[str, Any]
        if join_name:
            join_dir = self.workdir / "artifacts" / f"{join_name.lower()}_run"
            join_dir.mkdir(parents=True, exist_ok=True)
            join_req = {
                "task_id": f"{self.request.case_id}_{join_name.lower()}",
                "capability": "join_branches",
                "input": {
                    "task_description": self.request.task.get("description", ""),
                    "task_meta": self.request.task,
                    "dataset": self.request.dataset,
                    "parameters": self.request.parameters,
                    "join_inputs": (self.request.join_agent or {}).get("inputs", {}) or {},
                    "branch_responses": branch_responses,
                },
                "output_dir": str(join_dir),
            }
            downstream_resp = self._run_adapter(join_name, join_req)
            (join_dir / "response.json").write_text(
                json.dumps(downstream_resp, indent=2), encoding="utf-8"
            )
        else:
            downstream_resp = branch_responses[names[-1]]

        # ---- integration: LLM wraps the join (or the last branch) -------
        integration_dir = self.workdir / "artifacts" / "integration"
        integration_dir.mkdir(parents=True, exist_ok=True)
        integration = self._integrate(
            upstream_name, branch_responses.get(upstream_name, {}),
            downstream_name, downstream_resp,
            out_dir=integration_dir,
        )

        return {
            "upstream_name": upstream_name,
            "downstream_name": downstream_name,
            "upstream_resp": branch_responses.get(upstream_name, {}),
            "downstream_resp": downstream_resp,
            "handoffs": handoffs,
            "integration": integration,
            "branch_responses": branch_responses,
        }

    # ---- main run --------------------------------------------------------

    def run(self) -> CollaborationResult:
        t0 = time.monotonic()
        self._profile_repos()
        sandbox_report = self._build_sandboxes()
        self._register_cards()

        # Auto-resolve declared Python deps before invoking adapters.
        env_report = self._ensure_env()

        # Compose the workflow graph (described, not LLM-compiled — we
        # ship a deterministic L7 topology so the run is reproducible).
        graph = self._compile_graph()
        (self.workdir / "compiled_workflow_graph.json").write_text(
            json.dumps(graph, indent=2), encoding="utf-8"
        )

        # Render PNG + DOT topology so reviewers can see how the agents
        # were wired without reading the JSON.
        try:
            topology_artifacts = render_topology(graph, out_dir=self.workdir,
                                                  title=f"{self.request.case_id} — workflow topology")
        except Exception as exc:
            self.notes.append(f"topology_viz: render failed — {exc}")
            topology_artifacts = {}

        # Topology dispatch. Linear-handoff is the CS1 default; the
        # parallel-then-join pattern adds CS2 and any future N-way fan-out
        # collaborations without rewriting the surrounding scaffolding.
        if len(self.registry.names()) < 2:
            self.notes.append("expected ≥2 agents; got fewer")
            return CollaborationResult(
                case_id=self.request.case_id,
                workdir=self.workdir,
                registry=self.registry,
                repo_profiles=self.profiles,
                handoffs=[],
                artifacts={},
                notes=self.notes,
                status="failed",
            )

        if self.request.topology == "parallel_then_join":
            exec_out = self._execute_parallel_then_join()
        else:
            exec_out = self._execute_linear_handoff()

        upstream_name = exec_out["upstream_name"]
        downstream_name = exec_out["downstream_name"]
        upstream_resp = exec_out["upstream_resp"]
        downstream_resp = exec_out["downstream_resp"]
        handoffs = exec_out["handoffs"]
        integration = exec_out["integration"]
        branch_responses = exec_out.get("branch_responses", {})

        # ---- final manifest ----------------------------------------------
        elapsed = time.monotonic() - t0
        upstream_response_path = (
            self.workdir / "artifacts" / f"{upstream_name.lower()}_run" / "response.json"
        )
        downstream_response_path = (
            self.workdir / "artifacts" / f"{downstream_name.lower()}_run" / "response.json"
        )
        downstream_input_path = (
            self.workdir / "artifacts" / f"{downstream_name.lower()}_input.json"
        )
        artifacts = {
            "compiled_workflow_graph": str(self.workdir / "compiled_workflow_graph.json"),
            "agent_registry": str(self.workdir / "agent_registry.json"),
            "sandbox_report": str(self.workdir / "manifests" / "docker_build_report.json"),
            "upstream_response": str(upstream_response_path),
            "downstream_input": str(downstream_input_path),
            "downstream_response": str(downstream_response_path),
            "final_hypothesis_report_md": integration.get("final_hypothesis_report_md", ""),
            "final_hypothesis_report_json": integration.get("final_hypothesis_report_json", ""),
        }
        # Per-agent extras
        for k, v in (upstream_resp.get("artifacts") or {}).items():
            artifacts[f"upstream__{k}"] = str(v)
        for k, v in (downstream_resp.get("artifacts") or {}).items():
            artifacts[f"downstream__{k}"] = str(v)
        # Per-branch responses (parallel_then_join only) — surface them so
        # the manifest enumerates every external agent that ran.
        for branch_name, branch_resp in branch_responses.items():
            if branch_name in (upstream_name, downstream_name):
                continue
            artifacts[f"branch_{branch_name.lower()}_response"] = str(
                self.workdir / "artifacts" / f"{branch_name.lower()}_run" / "response.json"
            )
            for k, v in (branch_resp.get("artifacts") or {}).items():
                artifacts[f"branch_{branch_name.lower()}__{k}"] = str(v)

        # Status decision: any failure → status reflects it.
        any_failed = (
            upstream_resp.get("status") == "failed"
            or downstream_resp.get("status") == "failed"
            or not all(h.ok for h in handoffs)
            or any((r or {}).get("status") == "failed" for r in branch_responses.values())
        )
        any_partial = (
            upstream_resp.get("status") == "partial"
            or downstream_resp.get("status") == "partial"
            or any((r or {}).get("status") == "partial" for r in branch_responses.values())
        )
        synthetic = bool(
            upstream_resp.get("synthetic_fallback")
            or downstream_resp.get("synthetic_fallback")
            or any((r or {}).get("synthetic_fallback") for r in branch_responses.values())
        )
        status = (
            "failed" if any_failed
            else "synthetic_fallback" if synthetic
            else "partial" if any_partial
            else "success"
        )

        repos_meta = {
            name: {
                "url": prof.repo_url,
                "commit": prof.commit_sha,
                "container": self.registry.get(name).container,
                "wrapper_strategy": prof.preferred_wrapper_strategy,
            }
            for name, prof in self.profiles.items()
        }

        # Structured outputs: collaboration_log.md + final_report.md +
        # topology pointers go into the manifest so the user has one
        # place to look.
        integrator_meta = integration.get("integrator_meta", {}) or {
            "model": "unknown",
            "reasoning_effort": "default",
            "tokens_in": 0,
            "tokens_out": 0,
        }
        env_dump = env_report.model_dump() if env_report is not None else None
        try:
            collab_log_path = write_collaboration_log(
                workdir=self.workdir,
                case_id=self.request.case_id,
                repos=repos_meta,
                upstream_name=upstream_name,
                downstream_name=downstream_name,
                upstream_resp=upstream_resp,
                downstream_resp=downstream_resp,
                handoffs=[h.model_dump(exclude_none=True) for h in handoffs],
                integrator_meta=integrator_meta,
                env_report=env_dump,
                sandbox_report=sandbox_report,
                elapsed_s=elapsed,
                status=status,
                notes=self.notes,
            )
        except Exception as exc:
            self.notes.append(f"collab_report: log write failed — {exc}")
            collab_log_path = None
        try:
            final_report_path = write_final_report(
                workdir=self.workdir,
                case_id=self.request.case_id,
                repos=repos_meta,
                upstream_name=upstream_name,
                downstream_name=downstream_name,
                upstream_resp=upstream_resp,
                downstream_resp=downstream_resp,
                handoffs=[h.model_dump(exclude_none=True) for h in handoffs],
                integrator_meta=integrator_meta,
                integrator_md_path=Path(integration.get("final_hypothesis_report_md", "")) if integration.get("final_hypothesis_report_md") else None,
                geneagent_md_path=Path((downstream_resp.get("artifacts") or {}).get("geneagent_report_md", "")) if (downstream_resp.get("artifacts") or {}).get("geneagent_report_md") else None,
                topology_artifacts=topology_artifacts,
                env_report=env_dump,
                elapsed_s=elapsed,
                status=status,
                raw_artifacts_root=self.workdir,
                notes=self.notes,
            )
        except Exception as exc:
            self.notes.append(f"collab_report: final report write failed — {exc}")
            final_report_path = None

        if collab_log_path:
            artifacts["collaboration_log_md"] = str(collab_log_path)
        if final_report_path:
            artifacts["final_report_md"] = str(final_report_path)
        for k, v in topology_artifacts.items():
            artifacts[k] = v
        if env_dump is not None:
            artifacts["env_manifest"] = str(self.workdir / "manifests" / "env_manifest.json")

        manifest = {
            "case_id": self.request.case_id,
            "topology": self.request.topology,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_s": round(elapsed, 2),
            "status": status,
            "no_docker": self.no_docker,
            "repos": repos_meta,
            "dataset": self.request.dataset,
            "task": self.request.task,
            "parameters": self.request.parameters,
            "join_agent": self.request.join_agent,
            "main_results": integration.get("main_results", {}),
            "handoffs": [h.model_dump(exclude_none=True) for h in handoffs],
            "branch_responses": {
                name: {
                    "status": (r or {}).get("status"),
                    "summary": (r or {}).get("summary"),
                    "main_results": (r or {}).get("main_results"),
                    "synthetic_fallback": (r or {}).get("synthetic_fallback", False),
                    "warnings": (r or {}).get("warnings", []),
                    "n_artifacts": len((r or {}).get("artifacts", {}) or {}),
                }
                for name, r in branch_responses.items()
            },
            "notes": self.notes,
            "artifacts": artifacts,
            "sandbox_report": sandbox_report,
            "env_report": env_dump,
            "integrator_meta": integrator_meta,
            "topology_artifacts": topology_artifacts,
            "structured_outputs": {
                "collaboration_log": str(collab_log_path) if collab_log_path else None,
                "final_report": str(final_report_path) if final_report_path else None,
                "topology_png": topology_artifacts.get("topology_png"),
                "topology_dot": topology_artifacts.get("topology_dot"),
            },
        }
        (self.workdir / "run_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        # Also dump a JSONL trace.
        (self.workdir / "traces").mkdir(parents=True, exist_ok=True)
        trace_path = self.workdir / "traces" / "execution_trace.jsonl"
        with trace_path.open("a", encoding="utf-8") as f:
            for ev in [
                {"event": "run_start", "case_id": self.request.case_id},
                {"event": "profile_done", "n_repos": len(self.profiles)},
                {"event": "sandbox_done", "report": sandbox_report.get("__compose__")},
                {"event": "upstream_done", "agent": upstream_name, "status": upstream_resp.get("status")},
                {"event": "broker_done", "n_handoffs": len(handoffs)},
                {"event": "downstream_done", "agent": downstream_name, "status": downstream_resp.get("status")},
                {"event": "integration_done", "status": status},
                {"event": "run_end", "elapsed_s": round(elapsed, 2), "status": status},
            ]:
                f.write(json.dumps(ev) + "\n")

        return CollaborationResult(
            case_id=self.request.case_id,
            workdir=self.workdir,
            registry=self.registry,
            repo_profiles=self.profiles,
            handoffs=handoffs,
            artifacts=artifacts,
            notes=self.notes,
            status=status,
        )

    # ---- compiled graph (deterministic; not LLM-driven) ------------------

    def _compile_graph(self) -> dict[str, Any]:
        if self.request.topology == "parallel_then_join":
            return self._compile_graph_parallel()
        return self._compile_graph_linear()

    def _compile_graph_linear(self) -> dict[str, Any]:
        names = list(self.registry.names()) or ["upstream_agent", "downstream_agent"]
        upstream, downstream = (names + names)[:2]
        return {
            "case_id": self.request.case_id,
            "topology_type": "external_repo_collaboration_l7_linear_handoff",
            "nodes": [
                {"id": "repo_profiler", "kind": "agentcoop_internal",
                 "role": "inspect external repositories"},
                {"id": "sandbox_builder", "kind": "agentcoop_internal",
                 "role": "render Dockerfile + compose; optionally build images"},
                {"id": "agent_registry", "kind": "agentcoop_internal",
                 "role": "register AgentCards loaded from YAML or built from RepoProfile"},
                {"id": f"{upstream}_run", "kind": "sandboxed_external_agent",
                 "agent": upstream,
                 "role": (self.registry.get(upstream).role if upstream in self.registry.cards else "")},
                {"id": "broker", "kind": "agentcoop_internal",
                 "role": "validate gene-set / typed handoff"},
                {"id": f"{downstream}_run", "kind": "sandboxed_external_agent",
                 "agent": downstream,
                 "role": (self.registry.get(downstream).role if downstream in self.registry.cards else "")},
                {"id": "integrator", "kind": "llm_backed_agent_node",
                 "role": "synthesize the two outputs into an evidence-linked report"},
                {"id": "reporter", "kind": "agentcoop_internal",
                 "role": "package artifacts + manifest"},
            ],
            "edges": [
                ["repo_profiler", "sandbox_builder"],
                ["sandbox_builder", "agent_registry"],
                ["agent_registry", f"{upstream}_run"],
                [f"{upstream}_run", "broker"],
                ["broker", f"{downstream}_run"],
                [f"{downstream}_run", "integrator"],
                ["integrator", "reporter"],
            ],
            "gated_repairs": {
                "broker": ["gene_set_validation_repair"],
                "integrator": ["evidence_completion_repair"],
            },
        }

    def _compile_graph_parallel(self) -> dict[str, Any]:
        """Parallel-then-join graph: every repo runs in its own branch,
        the join_agent (if declared) consumes all branch outputs, and
        the LLM integrator wraps the join result.
        """
        names = list(self.registry.names()) or ["agent_a", "agent_b"]
        join_name = (self.request.join_agent or {}).get("name") or ""
        join_role = (self.request.join_agent or {}).get("role_hint") or "join + evaluation"
        nodes: list[dict[str, Any]] = [
            {"id": "repo_profiler", "kind": "agentcoop_internal",
             "role": "inspect external repositories"},
            {"id": "sandbox_builder", "kind": "agentcoop_internal",
             "role": "render Dockerfile + compose per repo"},
            {"id": "agent_registry", "kind": "agentcoop_internal",
             "role": "register AgentCards"},
            {"id": "data_inspector", "kind": "agentcoop_internal",
             "role": "schema inspection + barcode/cell-type alignment"},
        ]
        for name in names:
            nodes.append({
                "id": f"{name}_run", "kind": "sandboxed_external_agent",
                "agent": name,
                "role": (self.registry.get(name).role if name in self.registry.cards else ""),
            })
        nodes.append({
            "id": "broker", "kind": "agentcoop_internal",
            "role": "validate per-branch typed artifacts before join",
        })
        if join_name:
            nodes.append({
                "id": "join_agent", "kind": "evaluator_gate",
                "agent": join_name, "role": join_role,
            })
        nodes.append({
            "id": "integrator", "kind": "llm_backed_agent_node",
            "role": "synthesize per-branch + join evidence into one report",
        })
        nodes.append({
            "id": "reporter", "kind": "agentcoop_internal",
            "role": "package artifacts + manifest",
        })

        edges: list[list[str]] = [
            ["repo_profiler", "sandbox_builder"],
            ["sandbox_builder", "agent_registry"],
            ["agent_registry", "data_inspector"],
        ]
        for name in names:
            edges.append(["data_inspector", f"{name}_run"])
            edges.append([f"{name}_run", "broker"])
        if join_name:
            edges.append(["broker", "join_agent"])
            edges.append(["join_agent", "integrator"])
        else:
            edges.append(["broker", "integrator"])
        edges.append(["integrator", "reporter"])

        return {
            "case_id": self.request.case_id,
            "topology_type": "external_repo_collaboration_l7_parallel_then_join",
            "nodes": nodes,
            "edges": edges,
            "gated_repairs": {
                "data_inspector": ["barcode_alignment_repair", "label_alias_repair"],
                "broker": ["typed_artifact_validation_repair"],
                **({"join_agent": ["alias_mapping_repair", "tissue_filter_repair"]} if join_name else {}),
                "integrator": ["evidence_completion_repair"],
            },
        }

    # ---- integrator (LLM-backed) -----------------------------------------

    def _integrate(
        self,
        upstream_name: str, upstream_resp: dict[str, Any],
        downstream_name: str, downstream_resp: dict[str, Any],
        *, out_dir: Path,
    ) -> dict[str, Any]:
        """LLM-backed final synthesis.

        Uses the existing OpenAIClient if `OPENAI_API_KEY` is set; falls back
        to a deterministic stub otherwise so the pipeline always completes.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt = _build_integration_prompt(
            self.request.task,
            upstream_name, upstream_resp,
            downstream_name, downstream_resp,
        )

        api_key = os.environ.get("OPENAI_API_KEY")
        model = os.environ.get(
            "AGENTCOOP_REPO_COLLAB_MODEL",
            self.request.raw.get("model") or "gpt-5",
        )
        reasoning_effort = os.environ.get(
            "AGENTCOOP_REPO_COLLAB_EFFORT",
            self.request.raw.get("reasoning_effort") or "medium",
        )

        text: str
        usage: dict[str, Any] = {}
        if api_key:
            try:
                from agentcoop.backends.llm import OpenAIClient
                client = OpenAIClient(model=model, api_key=api_key)
                raw = asyncio.run(client.complete(
                    system="You synthesize evidence from two collaborating bioinformatics agents into one auditable hypothesis report.",
                    user=prompt,
                    response_format=None,
                    temperature=1.0,
                    max_tokens=4096,
                    reasoning_effort=reasoning_effort,
                ))
                text = (raw.get("text", "") or "").strip()
                usage = {
                    "tokens_in": raw.get("tokens_in"),
                    "tokens_out": raw.get("tokens_out"),
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                }
            except Exception as exc:
                self.notes.append(f"integrator LLM call failed; using stub. err={exc}")
                text = _stub_integration_text(upstream_name, upstream_resp, downstream_name, downstream_resp)
        else:
            self.notes.append("OPENAI_API_KEY not set; using deterministic stub for integration")
            text = _stub_integration_text(upstream_name, upstream_resp, downstream_name, downstream_resp)

        md_path = out_dir / "final_hypothesis_report.md"
        md_path.write_text(text, encoding="utf-8")
        json_path = out_dir / "final_hypothesis_report.json"
        json_path.write_text(json.dumps({
            "case_id": self.request.case_id,
            "upstream_agent": upstream_name,
            "downstream_agent": downstream_name,
            "report_md_path": str(md_path),
            "usage": usage,
            "task": self.request.task,
            "main_results": {
                "upstream_summary": upstream_resp.get("summary", ""),
                "downstream_summary": downstream_resp.get("summary", ""),
            },
        }, indent=2), encoding="utf-8")

        # Short summary for the manifest.
        summary_path = out_dir / "final_summary.md"
        summary_path.write_text(
            f"# {self.request.case_id} — final summary\n\n"
            f"**{upstream_name}** -> **{downstream_name}** collaboration\n\n"
            f"- Upstream summary: {upstream_resp.get('summary', '')}\n"
            f"- Downstream summary: {downstream_resp.get('summary', '')}\n\n"
            f"See `final_hypothesis_report.md` for the full integrator output.\n",
            encoding="utf-8",
        )

        return {
            "final_hypothesis_report_md": str(md_path),
            "final_hypothesis_report_json": str(json_path),
            "main_results": {
                "upstream_summary": upstream_resp.get("summary", ""),
                "downstream_summary": downstream_resp.get("summary", ""),
                "model": usage.get("model"),
                "tokens": (usage.get("tokens_in", 0) or 0) + (usage.get("tokens_out", 0) or 0),
            },
            "integrator_meta": {
                "model": usage.get("model") or model,
                "reasoning_effort": reasoning_effort,
                "tokens_in": int(usage.get("tokens_in") or 0),
                "tokens_out": int(usage.get("tokens_out") or 0),
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].replace(".git", "")


def _runtime_image_for(spec: SandboxSpec) -> str:
    """Pick the Docker image to invoke for a given spec.

    Honours `spec.model_extra["runtime_image"]` (set via the request's
    per-repo `sandbox_overrides`) so R-based or GPU-based agents can
    swap images without touching the orchestrator. Defaults to the
    generic Python runtime.
    """
    extra = getattr(spec, "model_extra", None) or {}
    return str(extra.get("runtime_image") or DEFAULT_RUNTIME_IMAGE)


def _build_integration_prompt(
    task: dict[str, Any],
    upstream_name: str, upstream_resp: dict[str, Any],
    downstream_name: str, downstream_resp: dict[str, Any],
) -> str:
    return (
        "You are the AgentCo-Op final integrator for a two-agent external "
        "repository collaboration.\n\n"
        f"TASK:\n{json.dumps(task, indent=2)}\n\n"
        f"UPSTREAM AGENT ({upstream_name}) RESPONSE:\n"
        f"{json.dumps(upstream_resp, indent=2)[:4000]}\n\n"
        f"DOWNSTREAM AGENT ({downstream_name}) RESPONSE:\n"
        f"{json.dumps(downstream_resp, indent=2)[:4000]}\n\n"
        "Produce a hypothesis report in Markdown that:\n"
        "1. Restates the biological / scientific question.\n"
        "2. Summarises the upstream agent's evidence (stats, artifacts).\n"
        "3. Summarises the downstream agent's interpretation.\n"
        "4. States the final conclusion with appropriate caution.\n"
        "5. Lists every artifact path mentioned in the responses.\n"
        "6. Lists limitations and caveats explicitly.\n"
    )


def _stub_integration_text(
    upstream_name: str, upstream_resp: dict[str, Any],
    downstream_name: str, downstream_resp: dict[str, Any],
) -> str:
    return (
        f"# AgentCo-Op stub integration\n\n"
        f"## Upstream — {upstream_name}\n\n"
        f"- status: {upstream_resp.get('status', 'unknown')}\n"
        f"- summary: {upstream_resp.get('summary', '')}\n\n"
        f"## Downstream — {downstream_name}\n\n"
        f"- status: {downstream_resp.get('status', 'unknown')}\n"
        f"- summary: {downstream_resp.get('summary', '')}\n\n"
        f"_Stub generated because no OpenAI API key was available; rerun with "
        f"`OPENAI_API_KEY` set to get a real synthesis from the GPT-5 thinking model._\n"
    )


__all__ = [
    "CollaborationRequest",
    "CollaborationResult",
    "RepoCollaborationOrchestrator",
    "register_local_adapter",
    "get_local_adapter",
]
