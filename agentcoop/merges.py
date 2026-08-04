"""Merge algebras — the registry that makes ``Join`` expressible.

A `Join` term cannot be constructed without naming a registered merge. That
constraint is the whole point of this module. In v1, whenever two agents
disagreed the answer was "vote", and voting got applied to whatever came back
regardless of whether it was a set of genes, a numeric estimate, or a piece of
prose. Majority voting over gene sets is not a neutral default: it silently
discards a marker found by one assay and not the other, which is precisely the
signal a cross-modality study exists to find.

So each merge declares:

* which artifact types it applies to,
* what it *means* (stated in prose, because a reviewer has to judge it),
* whether the branches must agree on their semantic facets,
* how many branches it needs.

`can_join` in the compiler consults this registry. No entry, no join.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import Artifact, FacetSet


#: Given the branch artifacts and the merge config, produce the merged payload.
MergeFn = Callable[[Sequence[Artifact], dict[str, Any]], Any]


class MergeSpec(BaseModel):
    """A named, executable way of combining concurrent branch outputs."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    #: Artifact type this merge is defined for. ``"*"`` means type-agnostic.
    applies_to: str
    #: What the merge *means*, in words a reviewer can evaluate. Required:
    #: an unexplained merge is an unauditable design decision.
    semantics: str
    fn: Optional[MergeFn] = Field(default=None, exclude=True)
    min_branches: int = 2
    max_branches: Optional[int] = None
    #: When true, branches must carry identical semantic facets or the merge is
    #: refused. Intersecting an HGNC gene set with an Ensembl one produces an
    #: empty set that looks like a scientific finding.
    requires_matching_facets: bool = True
    #: Facet keys allowed to differ even when the above is set — the modality
    #: axis is exactly what a cross-modality join is combining *over*.
    facet_exemptions: list[str] = Field(default_factory=lambda: ["modality", "assay"])

    def accepts_type(self, type_name: str) -> bool:
        return self.applies_to in ("*", type_name)

    def accepts_arity(self, n: int) -> bool:
        if n < self.min_branches:
            return False
        return self.max_branches is None or n <= self.max_branches


class MergeRefusal(ValueError):
    """The merge is registered but cannot legally be applied to these inputs."""


def facet_disagreements(
    artifacts: Sequence[Artifact], *, exemptions: Iterable[str] = ()
) -> dict[str, list[str]]:
    """Facet keys on which the branches disagree, ignoring ``exemptions``.

    Reported per key with the distinct values, so the error message names the
    actual conflict rather than saying "facets differ".
    """
    exempt = set(exemptions)
    keys: set[str] = set()
    for art in artifacts:
        keys.update(art.facets)
    out: dict[str, list[str]] = {}
    for key in sorted(keys - exempt):
        values = sorted({a.facets.get(key, "<undeclared>") for a in artifacts})
        if len(values) > 1:
            out[key] = values
    return out


class MergeRegistry:
    """Name -> merge. A plain object so tests and probes stay isolated."""

    def __init__(self, specs: Iterable[MergeSpec] = ()) -> None:
        self._specs: dict[str, MergeSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: MergeSpec) -> MergeSpec:
        if not spec.semantics.strip():
            raise ValueError(f"merge '{spec.name}' must state its semantics")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> Optional[MergeSpec]:
        return self._specs.get(name)

    def has(self, name: str) -> bool:
        return name in self._specs

    def names(self) -> list[str]:
        return sorted(self._specs)

    def for_type(self, type_name: str) -> list[MergeSpec]:
        """Merges legally defined for ``type_name``, name-sorted."""
        return sorted(
            (s for s in self._specs.values() if s.accepts_type(type_name)),
            key=lambda s: s.name,
        )

    def check_applicable(
        self, name: str, artifacts: Sequence[Artifact]
    ) -> tuple[bool, str]:
        """Can this merge legally be applied here? Returns ``(ok, reason)``."""
        spec = self.get(name)
        if spec is None:
            return False, f"no merge algebra named '{name}' is registered"
        if not artifacts:
            return False, f"merge '{name}' received no branch artifacts"

        type_names = {a.type_name for a in artifacts}
        if len(type_names) > 1:
            return False, (
                f"merge '{name}' received mixed artifact types: "
                + ", ".join(sorted(type_names))
            )
        type_name = next(iter(type_names))
        if not spec.accepts_type(type_name):
            return False, f"merge '{name}' is not defined for artifact type '{type_name}'"
        if not spec.accepts_arity(len(artifacts)):
            return False, (
                f"merge '{name}' requires {spec.min_branches}"
                + (f"-{spec.max_branches}" if spec.max_branches else "+")
                + f" branches, got {len(artifacts)}"
            )
        if spec.requires_matching_facets:
            clashes = facet_disagreements(artifacts, exemptions=spec.facet_exemptions)
            if clashes:
                detail = "; ".join(f"{k}: {', '.join(v)}" for k, v in clashes.items())
                return False, (
                    f"merge '{name}' requires matching facets but branches disagree ({detail}); "
                    "merging across this mismatch would fabricate a result"
                )
        return True, ""

    def apply(
        self, name: str, artifacts: Sequence[Artifact], config: Optional[dict[str, Any]] = None
    ) -> Any:
        """Apply the merge, refusing rather than guessing when it does not fit."""
        ok, reason = self.check_applicable(name, artifacts)
        if not ok:
            raise MergeRefusal(reason)
        spec = self.get(name)
        if spec is None or spec.fn is None:
            raise MergeRefusal(f"merge '{name}' has no implementation bound")
        return spec.fn(list(artifacts), dict(config or {}))


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _entries(artifact: Artifact, key: Optional[str]) -> list[Any]:
    """Extract the list of entries a set-like merge operates over."""
    payload = artifact.payload
    if key and isinstance(payload, dict):
        value = payload.get(key)
        return list(value) if isinstance(value, (list, tuple)) else []
    if isinstance(payload, (list, tuple)):
        return list(payload)
    if isinstance(payload, dict):
        # Single list-valued field is unambiguous; more than one is not, and
        # guessing which one mattered is how silent errors get introduced.
        list_fields = [k for k, v in sorted(payload.items()) if isinstance(v, list)]
        if len(list_fields) == 1:
            return list(payload[list_fields[0]])
    return []


def _entry_key(artifact: Artifact, config: dict[str, Any]) -> Optional[str]:
    key = config.get("entry_key")
    return str(key) if key else None


def _rebuild(artifact: Artifact, key: Optional[str], values: list[Any]) -> Any:
    """Put merged values back into the payload shape the branches used."""
    if key and isinstance(artifact.payload, dict):
        return {**artifact.payload, key: values}
    if isinstance(artifact.payload, dict):
        list_fields = [k for k, v in sorted(artifact.payload.items()) if isinstance(v, list)]
        if len(list_fields) == 1:
            return {**artifact.payload, list_fields[0]: values}
    return values


# ---------------------------------------------------------------------------
# Built-in merges
# ---------------------------------------------------------------------------


def _intersect(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    key = _entry_key(artifacts[0], config)
    sets = [_entries(a, key) for a in artifacts]
    common = set(sets[0])
    for other in sets[1:]:
        common &= set(other)
    ordered = [x for x in sets[0] if x in common]
    return _rebuild(artifacts[0], key, ordered)


def _union(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    key = _entry_key(artifacts[0], config)
    seen: list[Any] = []
    known: set[Any] = set()
    for art in artifacts:
        for entry in _entries(art, key):
            if entry not in known:
                known.add(entry)
                seen.append(entry)
    return _rebuild(artifacts[0], key, seen)


def _agreement(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    """Keep entries supported by at least ``min_support`` branches."""
    key = _entry_key(artifacts[0], config)
    threshold = int(config.get("min_support", 2))
    counts: dict[Any, int] = {}
    order: list[Any] = []
    for art in artifacts:
        for entry in dict.fromkeys(_entries(art, key)):
            if entry not in counts:
                order.append(entry)
            counts[entry] = counts.get(entry, 0) + 1
    kept = [e for e in order if counts[e] >= threshold]
    return _rebuild(artifacts[0], key, kept)


def _majority_vote(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    """Most common scalar payload; ties broken by first appearance.

    Registered deliberately with ``min_branches=3``: a two-way "vote" is just a
    tie, and calling it a vote hides that no decision procedure exists.
    """
    tally: dict[str, int] = {}
    first: dict[str, Any] = {}
    for art in artifacts:
        token = str(art.payload)
        tally[token] = tally.get(token, 0) + 1
        first.setdefault(token, art.payload)
    best = max(tally.items(), key=lambda kv: (kv[1], -list(tally).index(kv[0])))
    return first[best[0]]


def _mean(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    values = [a.payload for a in artifacts if isinstance(a.payload, (int, float))]
    if not values:
        raise MergeRefusal("numeric_mean received no numeric payloads")
    return sum(values) / len(values)


def _concat_records(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    key = _entry_key(artifacts[0], config)
    rows: list[Any] = []
    for art in artifacts:
        rows.extend(_entries(art, key))
    return _rebuild(artifacts[0], key, rows)


def _first_non_empty(artifacts: Sequence[Artifact], config: dict[str, Any]) -> Any:
    for art in artifacts:
        if art.payload not in (None, "", [], {}, ()):
            return art.payload
    raise MergeRefusal("first_non_empty found no non-empty branch payload")


BUILTIN_MERGES: list[MergeSpec] = [
    MergeSpec(
        name="intersect",
        applies_to="*",
        semantics=(
            "Entries present in every branch, in the first branch's order. Use when "
            "corroboration by all branches is the evidentiary claim being made. Note "
            "this is conservative: a real finding seen by only one assay is discarded."
        ),
        fn=_intersect,
    ),
    MergeSpec(
        name="union",
        applies_to="*",
        semantics=(
            "Every entry from any branch, de-duplicated, first-seen order. Use when "
            "branches are complementary observations and missing a finding is worse "
            "than admitting an unreplicated one."
        ),
        fn=_union,
    ),
    MergeSpec(
        name="agreement_filter",
        applies_to="*",
        semantics=(
            "Entries supported by at least `min_support` branches. The tunable "
            "middle ground between intersect and union; `min_support` must be "
            "declared rather than defaulted silently."
        ),
        fn=_agreement,
    ),
    MergeSpec(
        name="concat_records",
        applies_to="*",
        semantics=(
            "Row-wise concatenation preserving duplicates. Use when branches "
            "partition the data rather than replicate it."
        ),
        fn=_concat_records,
    ),
    MergeSpec(
        name="majority_vote",
        applies_to="*",
        semantics=(
            "The most frequent scalar answer. Requires at least three branches, "
            "because a two-way vote is a tie with no decision procedure. Only valid "
            "when branches are genuinely independent estimators of the same quantity."
        ),
        fn=_majority_vote,
        min_branches=3,
    ),
    MergeSpec(
        name="numeric_mean",
        applies_to="*",
        semantics=(
            "Arithmetic mean of numeric branch payloads. Valid only when branches "
            "estimate the same quantity on the same scale."
        ),
        fn=_mean,
    ),
    MergeSpec(
        name="first_non_empty",
        applies_to="*",
        semantics=(
            "First branch with a non-empty payload, in declared order. A precedence "
            "rule, not a combination: use for prioritized fallback, never to paper "
            "over a branch that failed silently."
        ),
        fn=_first_non_empty,
        requires_matching_facets=False,
    ),
]


def default_merge_registry() -> MergeRegistry:
    return MergeRegistry(BUILTIN_MERGES)


__all__ = [
    "MergeFn",
    "MergeSpec",
    "MergeRegistry",
    "MergeRefusal",
    "BUILTIN_MERGES",
    "default_merge_registry",
    "facet_disagreements",
]
