"""GeneAgent local-Python adapter.

Calls the OpenAI Chat Completions API directly to deliver a gene-set
analysis report shaped per `docs/experiments/case_study_1.md` §11.4. Designed to mirror
the upstream GeneAgent's role-based prompt + self-verification protocol
without requiring its old `openai==0.28.0` SDK or container.

If `OPENAI_API_KEY` is unset, falls back to a deterministic stub so the
case study completes; the stub clearly marks itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any


_GENEAGENT_SYSTEM = textwrap.dedent("""\
    You are GeneAgent: a gene-set interpretation specialist that consults
    biological-process knowledge (cardiac development, ECM remodelling,
    sarcomere structure, conduction-system specification, growth-factor
    signalling, etc.). You annotate human gene sets, group them into
    biologically coherent subprocesses, list supporting genes per
    subprocess, and self-verify before answering. Your output is concise,
    structured, and avoids any clinical claims.
""")


_GENEAGENT_USER_TEMPLATE = textwrap.dedent("""\
    Analyse this human marker-gene set and return a structured JSON report.

    GENE_SET (n={n}): {genes_list}

    BIOLOGICAL_CONTEXT:
    {biological_context}

    CONTRAST:
    {contrast_description}

    Respond as a JSON object with exactly these top-level keys (no commentary
    outside the JSON):
    {{
      "process_label": str,                 // one short label (e.g. "Cardiac Development and Remodeling")
      "subprocesses": [                     // 4–6 entries; ordered by support
        {{
          "name": str,
          "supporting_genes": [str],
          "evidence_summary": str
        }}
      ],
      "self_verification": {{
        "databases_or_sources_used": [str],
        "verification_summary": str
      }},
      "caveats": [str],
      "final_interpretation": str
    }}
""")


def invoke_geneagent_local(req: dict[str, Any]) -> dict[str, Any]:
    """Adapter entrypoint registered with the orchestrator."""
    inputs = req.get("input", {}) or {}
    out_dir = Path(req.get("output_dir", "runs/case1/heart_merfish/artifacts/geneagent_run"))
    out_dir.mkdir(parents=True, exist_ok=True)

    genes = list(inputs.get("gene_set", []) or [])
    bio_ctx = str(inputs.get("biological_context", "") or "")
    contrast = str(inputs.get("contrast_description", "") or "")
    organism = str(inputs.get("organism", "human"))

    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get(
        "AGENTCOOP_REPO_COLLAB_MODEL", os.environ.get("AGENTCOOP_GENEAGENT_MODEL", "gpt-5")
    )
    reasoning_effort = os.environ.get(
        "AGENTCOOP_REPO_COLLAB_EFFORT", os.environ.get("AGENTCOOP_GENEAGENT_EFFORT", "medium")
    )

    response_obj: dict[str, Any]
    raw_text = ""
    usage: dict[str, Any] = {}
    warnings: list[str] = []
    used_llm = False

    if not genes:
        warnings.append("empty gene_set received — falling back to stub")
        response_obj = _stub_report(genes, bio_ctx, contrast, reason="empty gene_set")
    elif not api_key:
        warnings.append("OPENAI_API_KEY not set — falling back to stub")
        response_obj = _stub_report(genes, bio_ctx, contrast, reason="no_api_key")
    else:
        try:
            from agentcoop.backends.llm import OpenAIClient

            client = OpenAIClient(model=model, api_key=api_key)
            user_prompt = _GENEAGENT_USER_TEMPLATE.format(
                n=len(genes),
                genes_list=", ".join(genes[:200]),  # cap visible list at 200
                biological_context=bio_ctx,
                contrast_description=contrast,
            )
            raw = asyncio.run(client.complete(
                system=_GENEAGENT_SYSTEM,
                user=user_prompt,
                response_format={"type": "json_object"},
                temperature=1.0,
                max_tokens=8000,
                reasoning_effort=reasoning_effort,
            ))
            raw_text = (raw.get("text") or "").strip()
            usage = {
                "tokens_in": raw.get("tokens_in"),
                "tokens_out": raw.get("tokens_out"),
                "model": raw.get("model"),
                "reasoning_effort": reasoning_effort,
            }
            response_obj = _parse_geneagent_json(raw_text)
            used_llm = True
        except Exception as exc:
            warnings.append(f"GeneAgent LLM call failed; using stub. err={exc}")
            response_obj = _stub_report(genes, bio_ctx, contrast, reason=str(exc))

    # Persist artifacts
    md_path = out_dir / "geneagent_report.md"
    md_path.write_text(_render_markdown(response_obj, genes), encoding="utf-8")
    json_path = out_dir / "geneagent_report.json"
    full_json = {
        "status": "success" if used_llm else "partial",
        "process_label": response_obj.get("process_label", ""),
        "subprocesses": response_obj.get("subprocesses", []),
        "self_verification": response_obj.get("self_verification", {}),
        "caveats": response_obj.get("caveats", []),
        "final_interpretation": response_obj.get("final_interpretation", ""),
        "input_summary": {
            "n_genes": len(genes),
            "genes_preview": genes[:25],
            "organism": organism,
        },
        "usage": usage,
        "warnings": warnings,
    }
    json_path.write_text(json.dumps(full_json, indent=2), encoding="utf-8")
    if raw_text:
        (out_dir / "geneagent_raw.txt").write_text(raw_text, encoding="utf-8")

    summary = (
        f"{response_obj.get('process_label', 'gene-set interpretation')} — "
        f"{len(response_obj.get('subprocesses', []))} subprocesses; "
        f"sources: {', '.join((response_obj.get('self_verification') or {}).get('databases_or_sources_used', [])[:5])}"
    )

    return {
        "status": "success" if used_llm else "partial",
        "synthetic_fallback": not used_llm,
        "summary": summary,
        "warnings": warnings,
        "artifacts": {
            "geneagent_report_md": str(md_path),
            "geneagent_report_json": str(json_path),
        },
        "main_results": {
            "process_label": response_obj.get("process_label", ""),
            "n_subprocesses": len(response_obj.get("subprocesses", [])),
        },
    }


def register() -> None:
    """Register adapter under both `GeneAgent` (canonical card name) and the
    PascalCase variants users may declare in YAML."""
    from agentcoop.core.repo_collaboration import register_local_adapter

    register_local_adapter("GeneAgent")(invoke_geneagent_local)
    register_local_adapter("geneagent")(invoke_geneagent_local)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_geneagent_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}


def _render_markdown(report: dict[str, Any], genes: list[str]) -> str:
    label = report.get("process_label", "(no label)")
    subs = report.get("subprocesses", []) or []
    verif = report.get("self_verification", {}) or {}
    caveats = report.get("caveats", []) or []
    interp = report.get("final_interpretation", "")
    lines: list[str] = [
        f"# GeneAgent report — {label}",
        "",
        f"**Input gene set ({len(genes)} genes):** {', '.join(genes[:50])}"
        + (" …" if len(genes) > 50 else ""),
        "",
        "## Subprocesses",
        "",
    ]
    for s in subs:
        lines.append(f"### {s.get('name', '(unnamed subprocess)')}")
        sg = s.get("supporting_genes") or []
        lines.append(f"- supporting genes ({len(sg)}): {', '.join(sg[:25])}"
                     + (" …" if len(sg) > 25 else ""))
        lines.append(f"- evidence: {s.get('evidence_summary', '')}")
        lines.append("")
    lines += [
        "## Self-verification",
        "",
        f"- sources: {', '.join(verif.get('databases_or_sources_used', []) or [])}",
        f"- summary: {verif.get('verification_summary', '')}",
        "",
        "## Caveats",
        "",
    ]
    for c in caveats:
        lines.append(f"- {c}")
    lines += ["", "## Final interpretation", "", interp.strip(), ""]
    return "\n".join(lines)


def _stub_report(
    genes: list[str], bio_ctx: str, contrast: str, *, reason: str
) -> dict[str, Any]:
    """Deterministic fallback when the real LLM call cannot run."""
    return {
        "process_label": "Cardiac Development and Remodeling (stub)",
        "subprocesses": [
            {
                "name": "Cardiac morphogenesis and differentiation",
                "supporting_genes": [g for g in genes if g in {"HAND2", "GATA4", "TBX5", "MEF2C", "MYH7", "MYH6", "TNNT2", "TNNI3", "MYL2", "MYL3", "MYL7", "NPPA", "NPPB"}],
                "evidence_summary": "Stub — derived from a hard-coded cardiac-developmental marker list.",
            },
            {
                "name": "Sarcomere and structural organization",
                "supporting_genes": [g for g in genes if g in {"DES", "MYBPC3", "MYBPC1", "TPM1", "TPM2", "ACTN1", "ACTN2", "ACTC1"}],
                "evidence_summary": "Stub — derived from sarcomere component list.",
            },
            {
                "name": "Extracellular matrix remodeling and fibrotic signaling",
                "supporting_genes": [g for g in genes if g in {"COL1A1", "COL1A2", "COL3A1", "COL5A1", "COL6A2", "FN1", "DCN", "LUM", "POSTN", "VCAN", "MMP2", "MMP9", "TIMP1", "TIMP3"}],
                "evidence_summary": "Stub — derived from ECM/fibrosis marker list.",
            },
            {
                "name": "Conduction system specification or electrophysiological regulation",
                "supporting_genes": [g for g in genes if g in {"GJA1", "GJA5", "GJC1", "KCNQ1", "KCNH2", "SCN5A", "RYR2", "CASQ2", "PLN", "ATP2A2"}],
                "evidence_summary": "Stub — derived from cardiac conduction marker list.",
            },
            {
                "name": "Growth-factor signaling",
                "supporting_genes": [g for g in genes if g in {"IGFBP5", "IGF1", "IGF2", "IGFBP3", "TGFB1", "TGFB2", "PDGFA", "PDGFC", "BMP2", "BMP4", "BMP10", "FGF7", "FGF10", "VEGFA"}],
                "evidence_summary": "Stub — derived from growth-factor signalling list.",
            },
        ],
        "self_verification": {
            "databases_or_sources_used": ["stub fallback (no live DB queries)"],
            "verification_summary": f"Stub generated because: {reason}.",
        },
        "caveats": [
            "Stub interpretation; not a real LLM call.",
            "Subprocess assignments come from hard-coded gene lists, not a live ontology.",
        ],
        "final_interpretation": (
            "The marker set is consistent with a cardiac developmental, ECM-rich, "
            "conduction-associated atrial fibroblast state per the hard-coded gene "
            "lists used by this stub. Replace this with the LLM-backed adapter for "
            "an evidence-grounded reading."
        ),
    }


__all__ = ["invoke_geneagent_local", "register"]
