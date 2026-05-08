"""Case Study 1 IntegratorReviewer.

Reads: enrichment JSONs (up/down) + GeneAgent reports (up/down).
Calls OpenAI (via `agentcoop.backends.llm.OpenAIClient`) once with the
benchmark integrator prompt from docs/experiments/case_study.md §3.7-style guidance,
adapted for bulk RNA-seq + gene-set analysis.

Writes: `final_report.md` with the synthesized biological interpretation
and a JSON sidecar with structured claims so the verifier can flag
unsupported ones.

Usage:
    python scripts/case1_integrator.py runs/case1/airway
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentcoop.backends.llm import OpenAIClient  # noqa: E402


SYSTEM_PROMPT = (
    "You are the IntegratorReviewer for a bulk RNA-seq differential-expression "
    "case study. You receive: (1) up- and down-regulated marker gene sets, "
    "(2) ORA enrichment tables, and (3) a GeneAgent functional interpretation "
    "for each direction. Your job:\n"
    " - Cross-check the GeneAgent labels against the enrichment evidence; "
    "flag claims that are unsupported by the data.\n"
    " - Identify the dominant biological themes (signaling pathways, immune "
    "response, metabolism, etc.) and how the up/down directions relate.\n"
    " - Give a confidence rating per theme (high/medium/low).\n"
    "Respond as JSON: {\"summary\": str, \"themes\": [{\"theme\": str, "
    "\"direction\": \"up\"|\"down\", \"evidence\": [str], \"confidence\": "
    "\"high\"|\"medium\"|\"low\"}], \"unsupported_claims\": [str], "
    "\"final_interpretation\": str, \"rationale_summary\": str}.\n"
    "Do not invent gene-pathway mappings beyond what the inputs provide."
)


def _summarize_enrichment(blob: dict) -> dict:
    rows = blob.get("rows", [])[:8]  # top 8
    return {
        "set_size": blob.get("set_size"),
        "background_size": blob.get("background_size"),
        "top_pathways": [
            {
                "pathway": r.get("pathway"),
                "pvalue": r.get("pvalue"),
                "overlap": r.get("overlap_genes", [])[:6],
            }
            for r in rows
        ],
    }


def _summarize_geneagent(blob: dict) -> dict:
    return {
        "model": blob.get("model"),
        "functional_labels": blob.get("functional_labels"),
        "supporting_evidence": blob.get("supporting_evidence", [])[:6],
        "logs_summary": blob.get("logs_summary"),
    }


async def integrate(case_dir: Path) -> dict:
    enr_up = json.loads((case_dir / "enrichment" / "up.json").read_text())
    enr_down = json.loads((case_dir / "enrichment" / "down.json").read_text())
    ga_up = json.loads((case_dir / "geneagent_up.json").read_text())
    ga_down = json.loads((case_dir / "geneagent_down.json").read_text())

    payload = {
        "context": "Airway smooth muscle cells treated with dexamethasone "
        "(synthetic glucocorticoid) vs untreated control. Goal: identify "
        "the dominant biological response.",
        "up": {
            "enrichment": _summarize_enrichment(enr_up),
            "geneagent": _summarize_geneagent(ga_up),
        },
        "down": {
            "enrichment": _summarize_enrichment(enr_down),
            "geneagent": _summarize_geneagent(ga_down),
        },
    }

    client = OpenAIClient(model="gpt-4o-mini")
    raw = await client.complete(
        system=SYSTEM_PROMPT,
        user=json.dumps(payload, indent=2),
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=4096,
    )
    await client.aclose()

    text = raw.get("text", "") or "{}"
    try:
        report = json.loads(text)
    except Exception:
        report = {"summary": "(parse failed)", "raw": text}
    report["_tokens_in"] = raw.get("tokens_in", 0)
    report["_tokens_out"] = raw.get("tokens_out", 0)
    return report


def _render_markdown(report: dict, case_dir: Path) -> str:
    lines: list[str] = []
    lines.append("# Case Study 1 — Final Biological Report")
    lines.append("")
    lines.append(f"**Context:** Airway dexamethasone vs control (synthetic data; same column shape as `scripts/case1_airway_de.R`).")
    lines.append("")
    lines.append("## Summary")
    lines.append(str(report.get("summary", "")))
    lines.append("")
    lines.append("## Themes")
    for t in report.get("themes", []) or []:
        lines.append(
            f"- **{t.get('theme','?')}** ({t.get('direction','?')}, "
            f"confidence: {t.get('confidence','?')}). "
            f"Evidence: {', '.join(t.get('evidence', []) or [])}"
        )
    lines.append("")
    lines.append("## Final interpretation")
    lines.append(str(report.get("final_interpretation", "")))
    lines.append("")
    if report.get("unsupported_claims"):
        lines.append("## Verifier flags (unsupported claims)")
        for c in report["unsupported_claims"]:
            lines.append(f"- {c}")
        lines.append("")
    lines.append("## Token usage")
    lines.append(f"- input tokens: {report.get('_tokens_in', 0)}")
    lines.append(f"- output tokens: {report.get('_tokens_out', 0)}")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append(f"- DE results: `{case_dir / 'de_results.tsv'}`")
    lines.append(f"- gene sets: `{case_dir / 'gene_sets'}/`")
    lines.append(f"- enrichment: `{case_dir / 'enrichment'}/`")
    lines.append(f"- geneagent: `{case_dir / 'geneagent_up.json'}`, `{case_dir / 'geneagent_down.json'}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    case_dir = Path(argv[0]) if argv else Path("runs/case1/airway")
    if not (case_dir / "enrichment" / "up.json").exists():
        print(f"error: missing {case_dir / 'enrichment' / 'up.json'} — run the bio CLI first", file=sys.stderr)
        return 2
    if not os.environ.get("OPENAI_API_KEY"):
        print("error: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    report = asyncio.run(integrate(case_dir))
    (case_dir / "final_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = _render_markdown(report, case_dir)
    (case_dir / "final_report.md").write_text(md, encoding="utf-8")
    print(json.dumps({
        "out_md": str(case_dir / "final_report.md"),
        "out_json": str(case_dir / "final_report.json"),
        "tokens_in": report.get("_tokens_in", 0),
        "tokens_out": report.get("_tokens_out", 0),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
