"""Artifact broker.

Validates and normalizes typed artifacts as they pass between sandboxed
agents in the `external_repo_collaboration` topology. Per
`docs/experiments/case_study_1.md` §4.4, the broker should never pass an unvalidated
free-text gene list — it must check gene symbols, deduplicate, preserve
order, and emit both the full marker list and the filtered upregulated
marker subset.

This module is **additive**: nothing in agentcoop today imports it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field


ArtifactKindT = Literal[
    "gene_set",
    "csv_path",
    "json_path",
    "markdown_path",
    "image_path",
    "directory",
    "table",
    "scalar",
]


class ArtifactRef(BaseModel):
    """Typed reference to a single artifact emitted by an agent."""

    model_config = ConfigDict(extra="allow")

    kind: ArtifactKindT
    name: str
    payload: Any | None = None       # in-memory payload (lists, dicts)
    path: str | None = None          # filesystem path on the host
    schema_hint: str | None = None
    notes: list[str] = Field(default_factory=list)


class HandoffResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    artifact: ArtifactRef
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


_HUMAN_GENE_RE = re.compile(r"^[A-Z][A-Z0-9\-\.]{0,15}$")
_MOUSE_GENE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-\.]{0,15}$")


def validate_gene_set(
    genes: Iterable[str],
    *,
    organism: str = "human",
    drop_empty: bool = True,
    dedupe: bool = True,
    upper_human: bool = True,
) -> tuple[list[str], list[str]]:
    """Return `(clean_genes, warnings)`.

    - `human` genes coerced to uppercase A–Z, digits, `-`, `.` and length ≤16.
    - `mouse` genes accept the same charset but with title-case allowed.
    - Duplicates dropped while preserving first-seen order.
    """
    pat = _HUMAN_GENE_RE if organism.lower().startswith("h") else _MOUSE_GENE_RE
    clean: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for raw in genes:
        if raw is None:
            continue
        s = str(raw).strip()
        if drop_empty and not s:
            continue
        if upper_human and pat is _HUMAN_GENE_RE:
            s = s.upper()
        if not pat.match(s):
            warnings.append(f"dropped non-symbol: {raw!r}")
            continue
        if dedupe and s in seen:
            continue
        seen.add(s)
        clean.append(s)
    return clean, warnings


def validate_csv_path(path: str | Path, *, required_columns: Iterable[str] = ()) -> list[str]:
    """Lightweight CSV check — header includes all required columns."""
    p = Path(path)
    warnings: list[str] = []
    if not p.is_file():
        return [f"missing CSV: {p}"]
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            header = f.readline().strip()
    except Exception as exc:
        return [f"could not read CSV header at {p}: {exc}"]
    cols = {c.strip() for c in header.split(",")}
    for need in required_columns:
        if need not in cols:
            warnings.append(f"CSV {p.name} missing column {need}")
    return warnings


def validate_image_path(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.is_file():
        return [f"missing image: {p}"]
    if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
        return [f"unexpected image extension: {p.suffix}"]
    if p.stat().st_size == 0:
        return [f"empty image: {p}"]
    return []


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


class ArtifactBroker:
    """Coordinates typed handoffs between agents and writes audit records."""

    def __init__(self, audit_dir: str | Path | None = None) -> None:
        self.audit_dir = Path(audit_dir).expanduser().resolve() if audit_dir else None
        if self.audit_dir is not None:
            self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.history: list[HandoffResult] = []

    # ---- gene-set handoff ------------------------------------------------

    def handoff_gene_set(
        self,
        *,
        producer: str,
        consumer: str,
        genes: Iterable[str],
        organism: str = "human",
        biological_context: str = "",
        contrast_description: str = "",
        write_to: str | Path | None = None,
    ) -> HandoffResult:
        clean, warnings = validate_gene_set(genes, organism=organism)
        ok = len(clean) > 0
        payload = {
            "organism": organism,
            "gene_set_name": f"{producer}_to_{consumer}_genes",
            "gene_set": clean,
            "biological_context": biological_context,
            "contrast_description": contrast_description,
            "n_genes": len(clean),
            "n_dropped": len(list(genes)) - len(clean),
        }
        path: str | None = None
        if write_to is not None:
            wp = Path(write_to)
            wp.parent.mkdir(parents=True, exist_ok=True)
            wp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            path = str(wp)

        artifact = ArtifactRef(
            kind="gene_set",
            name=f"{producer}_to_{consumer}_gene_set",
            payload=payload,
            path=path,
            schema_hint="docs/experiments/case_study_1.md §8 geneagent_input_gene_set.json",
        )
        result = HandoffResult(ok=ok, artifact=artifact, warnings=warnings)
        self._record(result, kind="handoff_gene_set", producer=producer, consumer=consumer)
        return result

    # ---- csv handoff ------------------------------------------------------

    def handoff_csv(
        self,
        *,
        producer: str,
        consumer: str,
        path: str | Path,
        required_columns: Iterable[str] = (),
    ) -> HandoffResult:
        warnings = validate_csv_path(path, required_columns=required_columns)
        ok = not any(w.startswith("missing CSV") for w in warnings)
        artifact = ArtifactRef(
            kind="csv_path",
            name=f"{producer}_to_{consumer}_csv",
            path=str(path),
        )
        result = HandoffResult(ok=ok, artifact=artifact, warnings=warnings)
        self._record(result, kind="handoff_csv", producer=producer, consumer=consumer)
        return result

    # ---- generic file handoff --------------------------------------------

    def handoff_file(
        self,
        *,
        producer: str,
        consumer: str,
        path: str | Path,
        kind: ArtifactKindT = "json_path",
    ) -> HandoffResult:
        p = Path(path)
        warnings: list[str] = []
        if not p.exists():
            warnings.append(f"missing file: {p}")
        if kind in {"image_path"}:
            warnings.extend(validate_image_path(p))
        artifact = ArtifactRef(kind=kind, name=p.name, path=str(p))
        ok = p.exists()
        result = HandoffResult(ok=ok, artifact=artifact, warnings=warnings)
        self._record(result, kind="handoff_file", producer=producer, consumer=consumer)
        return result

    # ---- audit ------------------------------------------------------------

    def _record(self, result: HandoffResult, **extra: Any) -> None:
        self.history.append(result)
        if self.audit_dir is None:
            return
        log = self.audit_dir / "broker.jsonl"
        entry = {**extra, "ok": result.ok, "warnings": result.warnings, "artifact": result.artifact.model_dump(exclude_none=True)}
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


__all__ = [
    "ArtifactKindT",
    "ArtifactRef",
    "HandoffResult",
    "ArtifactBroker",
    "validate_gene_set",
    "validate_csv_path",
    "validate_image_path",
]
