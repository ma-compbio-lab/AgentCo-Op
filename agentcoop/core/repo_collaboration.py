"""External-repo-collaboration orchestration.

Loads a request YAML matching the schema in `case_study_1.md` §3.3,
profiles each repository, generates Dockerfiles + smoke tests via the
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
from agentcoop.core.repo_profile import RepoProfile, profile_repo
from agentcoop.core.sandbox_build import (
    SandboxBuilder,
    SandboxSpec,
    spec_from_profile,
)


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
                dry_run=True,  # always dry-run; let user run docker build manually
            )
            specs.append(spec)
            report[name] = {
                "image_built": res.image_built,
                "smoke_ok": res.smoke_ok,
                "dockerfile": str(res.dockerfile_path),
                "smoke_test": str(res.smoke_test_path),
                "notes": res.notes,
            }
        # Compose for both services together.
        compose_path = self.builder.write_compose(specs)
        report["__compose__"] = str(compose_path)
        (self.workdir / "manifests" / "docker_build_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report

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
        """Invoke the agent's adapter function. In `--no-docker` mode we look
        for a registered local adapter; with Docker we'd `docker exec` into
        the container's wrapper."""
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
        # Docker mode (left for future Session): call docker exec.
        container = self.registry.get(card_name).container
        cmd = ["docker", "exec", container, "python",
               f"/workspace/wrappers/{card_name.lower()}_worker.py",
               "--invoke-json", json.dumps(invocation)]
        try:
            r = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as exc:
            return {
                "status": "failed",
                "summary": f"docker invocation failed: {exc}",
                "artifacts": {},
                "warnings": [str(exc)],
            }

    # ---- main run --------------------------------------------------------

    def run(self) -> CollaborationResult:
        t0 = time.monotonic()
        self._profile_repos()
        sandbox_report = self._build_sandboxes()
        self._register_cards()

        # Compose the workflow graph (described, not LLM-compiled — we
        # ship a deterministic L7 topology so the run is reproducible).
        graph = self._compile_graph()
        (self.workdir / "compiled_workflow_graph.json").write_text(
            json.dumps(graph, indent=2), encoding="utf-8"
        )

        # Pick upstream / downstream agent in declared order.
        if len(self.registry.names()) < 2:
            self.notes.append("expected ≥2 agents; got fewer")
            status = "failed"
            return CollaborationResult(
                case_id=self.request.case_id,
                workdir=self.workdir,
                registry=self.registry,
                repo_profiles=self.profiles,
                handoffs=[],
                artifacts={},
                notes=self.notes,
                status=status,
            )

        upstream_name, downstream_name = self.registry.names()[:2]

        # ---- upstream invocation -----------------------------------------
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
            },
            "output_dir": str(upstream_dir),
        }
        upstream_resp = self._run_adapter(upstream_name, upstream_req)
        (upstream_dir / "response.json").write_text(
            json.dumps(upstream_resp, indent=2), encoding="utf-8"
        )

        # ---- broker handoff ----------------------------------------------
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

        # ---- downstream invocation ---------------------------------------
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

        # ---- integration: bring the responses together via the LLM client
        integration_dir = self.workdir / "artifacts" / "integration"
        integration_dir.mkdir(parents=True, exist_ok=True)
        integration = self._integrate(
            upstream_name, upstream_resp,
            downstream_name, downstream_resp,
            out_dir=integration_dir,
        )

        # ---- final manifest ----------------------------------------------
        elapsed = time.monotonic() - t0
        artifacts = {
            "compiled_workflow_graph": str(self.workdir / "compiled_workflow_graph.json"),
            "agent_registry": str(self.workdir / "agent_registry.json"),
            "sandbox_report": str(self.workdir / "manifests" / "docker_build_report.json"),
            "upstream_response": str(upstream_dir / "response.json"),
            "downstream_input": str(downstream_input_path),
            "downstream_response": str(downstream_dir / "response.json"),
            "final_hypothesis_report_md": integration.get("final_hypothesis_report_md", ""),
            "final_hypothesis_report_json": integration.get("final_hypothesis_report_json", ""),
        }
        # Per-agent extras
        for k, v in (upstream_resp.get("artifacts") or {}).items():
            artifacts[f"upstream__{k}"] = str(v)
        for k, v in (downstream_resp.get("artifacts") or {}).items():
            artifacts[f"downstream__{k}"] = str(v)

        # Status decision: any failure → status reflects it.
        any_failed = (
            upstream_resp.get("status") == "failed"
            or downstream_resp.get("status") == "failed"
            or not all(h.ok for h in handoffs)
        )
        any_partial = (
            upstream_resp.get("status") == "partial"
            or downstream_resp.get("status") == "partial"
        )
        synthetic = bool(
            upstream_resp.get("synthetic_fallback")
            or downstream_resp.get("synthetic_fallback")
        )
        status = (
            "failed" if any_failed
            else "synthetic_fallback" if synthetic
            else "partial" if any_partial
            else "success"
        )

        manifest = {
            "case_id": self.request.case_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_s": round(elapsed, 2),
            "status": status,
            "no_docker": self.no_docker,
            "repos": {
                name: {
                    "url": prof.repo_url,
                    "commit": prof.commit_sha,
                    "container": self.registry.get(name).container,
                    "wrapper_strategy": prof.preferred_wrapper_strategy,
                }
                for name, prof in self.profiles.items()
            },
            "dataset": self.request.dataset,
            "task": self.request.task,
            "main_results": integration.get("main_results", {}),
            "handoffs": [h.model_dump(exclude_none=True) for h in handoffs],
            "notes": self.notes,
            "artifacts": artifacts,
            "sandbox_report": sandbox_report,
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
        names = list(self.registry.names()) or ["upstream_agent", "downstream_agent"]
        upstream, downstream = (names + names)[:2]
        return {
            "case_id": self.request.case_id,
            "topology_type": "external_repo_collaboration_l7",
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
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].replace(".git", "")


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
