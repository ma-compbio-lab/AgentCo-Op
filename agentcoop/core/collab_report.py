"""Structured outputs for the external-repo collaboration runs.

Generates two human-facing artifacts on top of the JSON / JSONL the
orchestrator already writes:

1. `collaboration_log.md` — narrates each stage in order, with status,
   timing, decisions, and the artifact each stage produced. Useful when
   you want to *see* how AgentCo-Op composed the two external agents.
2. `final_report.md` — a single self-contained Markdown report. Embeds
   the topology PNG (relative path), the executive summary, the
   per-stage table, GeneAgent's biology report (full text), the
   integrator's hypothesis report (full text), and every artifact path
   so the report is the only file a reviewer needs to open.

Both files live at the run root next to `run_manifest.json`.
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_collaboration_log(
    *,
    workdir: Path,
    case_id: str,
    repos: dict[str, Any],
    upstream_name: str,
    downstream_name: str,
    upstream_resp: dict[str, Any],
    downstream_resp: dict[str, Any],
    handoffs: list[dict[str, Any]],
    integrator_meta: dict[str, Any],
    env_report: dict[str, Any] | None,
    sandbox_report: dict[str, Any],
    elapsed_s: float,
    status: str,
    notes: list[str],
) -> Path:
    """Persist a per-stage Markdown log narrating the collaboration."""
    out = workdir / "collaboration_log.md"
    lines: list[str] = []
    lines.append(f"# AgentCo-Op collaboration log — `{case_id}`")
    lines.append("")
    lines.append(f"- status: **{status}**")
    lines.append(f"- elapsed: {elapsed_s:.1f}s")
    lines.append(f"- workdir: `{workdir}`")
    lines.append("")

    # Stage 1 — environment configuration.
    lines.append("## 1. Environment configuration (auto)")
    lines.append("")
    if env_report:
        mode = env_report.get("mode", "local")
        lines.append(f"- mode: `{mode}`")
        lines.append(f"- python: `{env_report.get('python_executable')}` "
                     f"(v{env_report.get('python_version')})")
        lines.append("")
        lines.append("| Package | Requested | State | Installed version |")
        lines.append("|---|---|---|---|")
        for p in env_report.get("packages", []):
            lines.append(
                f"| {p.get('name')} | {p.get('requested')} | "
                f"{p.get('state')} | {p.get('installed_version') or '—'} |"
            )
        lines.append("")
    else:
        lines.append("- (no env report; nothing to install)")
        lines.append("")

    # Stage 2 — repo profiling.
    lines.append("## 2. Repo profiling")
    lines.append("")
    lines.append("| Repo | URL | Commit | Wrapper strategy |")
    lines.append("|---|---|---|---|")
    for name, meta in repos.items():
        lines.append(
            f"| {name} | {meta.get('url')} | "
            f"`{(meta.get('commit') or '')[:12]}` | "
            f"{meta.get('wrapper_strategy', '—')} |"
        )
    lines.append("")
    lines.append("Per-repo profiles: `manifests/repo_profile_<Repo>.json`.")
    lines.append("")

    # Stage 3 — sandbox build (Dockerfiles + compose).
    lines.append("## 3. Sandbox build")
    lines.append("")
    lines.append(f"Dockerfiles + smoke tests + `docker-compose.yml` written under `docker/`. "
                 f"Build report: `manifests/docker_build_report.json`.")
    if isinstance(sandbox_report, dict):
        for name, info in sandbox_report.items():
            if name.startswith("__") or not isinstance(info, dict):
                continue
            built = info.get("image_built")
            smoke = info.get("smoke_ok")
            lines.append(f"- **{name}**: image_built={built}, smoke_ok={smoke}")
            for note in (info.get("notes") or [])[:3]:
                lines.append(f"  - note: {note}")
    lines.append("")

    # Stage 4 — agent registration.
    lines.append("## 4. AgentCard registry")
    lines.append("")
    lines.append("`agent_registry.json` contains one card per repo with role, capabilities, "
                 "container reference, and the typed I/O schema the wrapper exposes.")
    lines.append("")

    # Stage 5 — upstream agent.
    lines.append(f"## 5. Upstream agent — `{upstream_name}`")
    lines.append("")
    lines.append(f"- status: **{upstream_resp.get('status', 'unknown')}**")
    lines.append(f"- summary: {upstream_resp.get('summary', '')}")
    if upstream_resp.get("synthetic_fallback"):
        lines.append(f"- ⚠️  synthetic_fallback: True")
    lines.append("- artifacts:")
    for k, v in (upstream_resp.get("artifacts") or {}).items():
        if k == "gene_set":
            n = len(v) if isinstance(v, list) else 0
            lines.append(f"  - **{k}**: list of {n} genes (in-memory; flattened to broker)")
        else:
            lines.append(f"  - **{k}**: `{v}`")
    if upstream_resp.get("main_results"):
        lines.append(f"- main_results: {json.dumps(upstream_resp['main_results'])}")
    lines.append("")

    # Stage 6 — broker.
    lines.append("## 6. Artifact broker")
    lines.append("")
    if not handoffs:
        lines.append("- (no handoffs recorded)")
    else:
        lines.append("| # | Kind | Producer → Consumer | OK | Warnings | Path |")
        lines.append("|--:|---|---|---|---|---|")
        for i, h in enumerate(handoffs, 1):
            artifact = h.get("artifact") or {}
            warns = "; ".join((h.get("warnings") or [])[:3]) or "—"
            kind = artifact.get("kind", "?")
            name = artifact.get("name", "?")
            path = artifact.get("path") or "—"
            lines.append(f"| {i} | {kind} | `{name}` | {h.get('ok')} | {warns} | `{path}` |")
    lines.append("")

    # Stage 7 — downstream agent.
    lines.append(f"## 7. Downstream agent — `{downstream_name}`")
    lines.append("")
    lines.append(f"- status: **{downstream_resp.get('status', 'unknown')}**")
    lines.append(f"- summary: {downstream_resp.get('summary', '')}")
    lines.append("- artifacts:")
    for k, v in (downstream_resp.get("artifacts") or {}).items():
        lines.append(f"  - **{k}**: `{v}`")
    lines.append("")

    # Stage 8 — integrator.
    lines.append("## 8. LLM integrator")
    lines.append("")
    lines.append(f"- model: `{integrator_meta.get('model', 'unknown')}` "
                 f"(reasoning_effort=`{integrator_meta.get('reasoning_effort', 'default')}`)")
    lines.append(f"- tokens (in/out): "
                 f"{integrator_meta.get('tokens_in', 0)} / {integrator_meta.get('tokens_out', 0)}")
    lines.append(f"- output: `artifacts/integration/final_hypothesis_report.md`")
    lines.append("")

    # Notes.
    if notes:
        lines.append("## Notes / warnings")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_final_report(
    *,
    workdir: Path,
    case_id: str,
    repos: dict[str, Any],
    upstream_name: str,
    downstream_name: str,
    upstream_resp: dict[str, Any],
    downstream_resp: dict[str, Any],
    handoffs: list[dict[str, Any]],
    integrator_meta: dict[str, Any],
    integrator_md_path: Path | None,
    geneagent_md_path: Path | None,
    topology_artifacts: dict[str, str],
    env_report: dict[str, Any] | None,
    elapsed_s: float,
    status: str,
    raw_artifacts_root: Path,
    notes: list[str],
) -> Path:
    """Write a single self-contained `final_report.md` under `workdir`."""
    out = workdir / "final_report.md"
    topology_png_rel = (
        Path(topology_artifacts.get("topology_png", "")).relative_to(workdir)
        if topology_artifacts.get("topology_png")
        else None
    )

    md: list[str] = []
    md.append(f"# AgentCo-Op final report — `{case_id}`")
    md.append("")
    md.append(f"- status: **{status}**")
    md.append(f"- workdir (raw outputs): `{raw_artifacts_root}`")
    md.append(f"- this report: `{out}`")
    md.append(f"- elapsed: {elapsed_s:.1f}s")
    md.append(f"- integrator model: `{integrator_meta.get('model', 'unknown')}` "
              f"(reasoning_effort=`{integrator_meta.get('reasoning_effort', 'default')}`)")
    md.append("")

    # Topology.
    md.append("## Compiled multi-agent topology")
    md.append("")
    if topology_png_rel:
        md.append(f"![topology]({topology_png_rel})")
    md.append("")
    md.append("Generated by `agentcoop.core.topology_viz.render_topology()` from "
              "`compiled_workflow_graph.json`. The DOT source is also written to "
              "`topology.dot` for users who prefer Graphviz rendering.")
    md.append("")

    # Repos involved.
    md.append("## Collaborating repositories")
    md.append("")
    md.append("| Repo | URL | Commit | Container |")
    md.append("|---|---|---|---|")
    for name, meta in repos.items():
        md.append(
            f"| {name} | {meta.get('url')} | "
            f"`{(meta.get('commit') or '')[:12]}` | "
            f"`{meta.get('container', '')}` |"
        )
    md.append("")

    # Environment configuration summary.
    md.append("## Environment configuration (handled internally by AgentCo-Op)")
    md.append("")
    if env_report:
        md.append(f"AgentCo-Op auto-resolved {len(env_report.get('packages', []))} "
                  f"declared Python packages before invoking the adapters; the manifest "
                  f"is at `manifests/env_manifest.json`.")
        md.append("")
        md.append("| Package | State | Installed version |")
        md.append("|---|---|---|")
        for p in env_report.get("packages", []):
            md.append(
                f"| {p.get('name')} | {p.get('state')} | "
                f"{p.get('installed_version') or '—'} |"
            )
    else:
        md.append("(No declared package requirements for this run.)")
    md.append("")

    # Per-stage executive summary.
    md.append("## Stage-by-stage execution")
    md.append("")
    md.append("| # | Stage | Status | Key artifact |")
    md.append("|--:|---|---|---|")
    md.append(f"| 1 | env_manager (auto pkg install) | `{('present' if env_report else 'n/a')}` | `manifests/env_manifest.json` |")
    md.append(f"| 2 | repo_profiler | `success` | `manifests/repo_profile_*.json` |")
    md.append(f"| 3 | sandbox_builder | `dry_run`/`built` | `docker/Dockerfile.*`, `docker/docker-compose.yml` |")
    md.append(f"| 4 | agent_registry | `success` | `agent_registry.json` |")
    md.append(f"| 5 | upstream `{upstream_name}` | `{upstream_resp.get('status', 'unknown')}` | `artifacts/{upstream_name.lower()}_run/` |")
    md.append(f"| 6 | broker | `{all(h.get('ok') for h in handoffs)}` | `manifests/broker.jsonl` |")
    md.append(f"| 7 | downstream `{downstream_name}` | `{downstream_resp.get('status', 'unknown')}` | `artifacts/{downstream_name.lower()}_run/` |")
    md.append(f"| 8 | integrator (`{integrator_meta.get('model', '?')}`) | `success` | `artifacts/integration/final_hypothesis_report.md` |")
    md.append("")

    # Upstream summary card.
    md.append(f"## Upstream — `{upstream_name}`")
    md.append("")
    md.append(f"**Summary.** {upstream_resp.get('summary', '')}")
    md.append("")
    if upstream_resp.get("main_results"):
        md.append("**Main results.**")
        md.append("")
        md.append("```json")
        md.append(json.dumps(upstream_resp["main_results"], indent=2))
        md.append("```")
        md.append("")
    md.append("**Artifacts.**")
    md.append("")
    for k, v in (upstream_resp.get("artifacts") or {}).items():
        if k == "gene_set":
            n = len(v) if isinstance(v, list) else 0
            md.append(f"- `{k}`: in-memory list of {n} genes (flattened to broker)")
        else:
            md.append(f"- `{k}`: `{v}`")
    md.append("")

    # Broker.
    md.append("## Artifact broker — typed handoffs")
    md.append("")
    if handoffs:
        md.append("| # | Kind | Producer → Consumer | OK | Warnings | Path |")
        md.append("|--:|---|---|---|---|---|")
        for i, h in enumerate(handoffs, 1):
            artifact = h.get("artifact") or {}
            warns = "; ".join((h.get("warnings") or [])[:3]) or "—"
            md.append(
                f"| {i} | {artifact.get('kind', '?')} | "
                f"`{artifact.get('name', '?')}` | {h.get('ok')} | "
                f"{warns} | `{artifact.get('path') or '—'}` |"
            )
    md.append("")

    # Downstream summary.
    md.append(f"## Downstream — `{downstream_name}`")
    md.append("")
    md.append(f"**Summary.** {downstream_resp.get('summary', '')}")
    md.append("")
    md.append("**Artifacts.**")
    md.append("")
    for k, v in (downstream_resp.get("artifacts") or {}).items():
        md.append(f"- `{k}`: `{v}`")
    md.append("")

    # Embed the GeneAgent biology report.
    if geneagent_md_path and Path(geneagent_md_path).is_file():
        md.append(f"### `{downstream_name}` biology report (full text)")
        md.append("")
        md.append("```markdown")
        md.append(Path(geneagent_md_path).read_text(encoding="utf-8").rstrip())
        md.append("```")
        md.append("")

    # Integrator hypothesis.
    md.append("## Integrator — final hypothesis report")
    md.append("")
    if integrator_md_path and Path(integrator_md_path).is_file():
        md.append("```markdown")
        md.append(Path(integrator_md_path).read_text(encoding="utf-8").rstrip())
        md.append("```")
    else:
        md.append("(integrator report missing)")
    md.append("")

    # Notes.
    if notes:
        md.append("## Notes / warnings")
        md.append("")
        for n in notes:
            md.append(f"- {n}")
        md.append("")

    # Reproducibility.
    md.append("## Reproducibility")
    md.append("")
    md.append("Re-running this case with the same request YAML produces the same "
              "deterministic stages; LLM-driven stages will vary slightly across calls.")
    md.append("")
    md.append("```bash")
    md.append("agentcoop collaborate \\")
    md.append("  --request case_study_1.request.yaml \\")
    md.append(f"  --workdir {raw_artifacts_root} \\")
    md.append("  --no-docker \\")
    md.append(f"  --model {integrator_meta.get('model', 'gpt-5')} \\")
    md.append(f"  --reasoning-effort {integrator_meta.get('reasoning_effort', 'medium')}")
    md.append("```")
    md.append("")

    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return out


__all__ = ["write_collaboration_log", "write_final_report"]
