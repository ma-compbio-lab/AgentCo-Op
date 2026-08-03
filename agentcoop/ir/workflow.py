"""The typed workflow grammar.

v1 let a designer emit an arbitrary node/edge JSON blob. The failure mode is
that nothing constrains *why* a shape was chosen, so "complex task" reliably
turns into "more agents". Here a workflow is a term in a small grammar, and
every production has an applicability condition that must be discharged with
evidence before it can be used (see :mod:`agentcoop.compile.grammar`).

    W ::= Atomic(component, subgoal)
        | Sequence(W1, ..., Wn)
        | Parallel(W1, ..., Wk)
        | Join(W1, ..., Wk; merge)
        | Verify(W; verifier)
        | Fallback(W_primary, W_alternate)
        | HumanGate(W; condition)

Terms are lowered to an execution graph for running and for static analysis.
The term tree is retained as the primary representation because repair
operates on it: replacing a subterm is a well-defined, reversible operation,
whereas mutating a node list in place is how v1 ended up with graphs nobody
could explain.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Iterator, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.evidence import EvidenceLedger


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------


class TermBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Stable identifier. Auto-derived from structure when omitted so that
    #: recompiling an unchanged workflow yields the same ids (needed for
    #: patch targeting, caching, and diffing two candidates).
    term_id: str = ""
    #: Free-form annotations carried into the manifest.
    notes: list[str] = Field(default_factory=list)

    def structural_key(self) -> str:
        raise NotImplementedError

    def ensure_ids(self, prefix: str = "t") -> "WorkflowTerm":
        """Assign deterministic ids to this term and all descendants."""
        raise NotImplementedError

    def children(self) -> list["WorkflowTerm"]:
        return []


def _hash_key(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


class Atomic(TermBase):
    """A single component invocation serving a single subgoal."""

    kind: Literal["atomic"] = "atomic"
    component: str
    subgoal_id: str
    config: dict[str, Any] = Field(default_factory=dict)

    def structural_key(self) -> str:
        return f"atomic({self.component},{self.subgoal_id})"

    def ensure_ids(self, prefix: str = "t") -> "Atomic":
        if self.term_id:
            return self
        return self.model_copy(
            update={"term_id": f"{self.subgoal_id}__{self.component}".replace(" ", "_")}
        )


class Sequence(TermBase):
    """Ordered composition. Downstream consumes upstream artifacts."""

    kind: Literal["sequence"] = "sequence"
    children_terms: list["WorkflowTerm"] = Field(default_factory=list)

    def structural_key(self) -> str:
        return "seq(" + ",".join(c.structural_key() for c in self.children_terms) + ")"

    def children(self) -> list["WorkflowTerm"]:
        return list(self.children_terms)

    def ensure_ids(self, prefix: str = "t") -> "Sequence":
        kids = [c.ensure_ids(prefix) for c in self.children_terms]
        tid = self.term_id or f"seq_{_hash_key(self.structural_key())}"
        return self.model_copy(update={"children_terms": kids, "term_id": tid})


class Parallel(TermBase):
    """Concurrent branches with no data dependency between them.

    ``independence_ref`` must point at the coordination-evidence item that
    established branch independence. The grammar rule refuses to build a
    Parallel without it, which is what stops "the task is complex, so fan out".
    """

    kind: Literal["parallel"] = "parallel"
    branches: list["WorkflowTerm"] = Field(default_factory=list)
    independence_ref: str = ""

    def structural_key(self) -> str:
        return "par(" + ",".join(sorted(b.structural_key() for b in self.branches)) + ")"

    def children(self) -> list["WorkflowTerm"]:
        return list(self.branches)

    def ensure_ids(self, prefix: str = "t") -> "Parallel":
        kids = [b.ensure_ids(prefix) for b in self.branches]
        tid = self.term_id or f"par_{_hash_key(self.structural_key())}"
        return self.model_copy(update={"branches": kids, "term_id": tid})


class Join(TermBase):
    """Merge concurrent branches through a *named, registered* merge algebra.

    A join without a merge function is not expressible in this grammar. That
    is intentional: "two agents disagreed, so vote" is only a valid design if
    voting is actually the right merge for the artifact type, and saying which
    merge applies forces that question to be answered.
    """

    kind: Literal["join"] = "join"
    branches: list["WorkflowTerm"] = Field(default_factory=list)
    merge: str = ""
    merge_config: dict[str, Any] = Field(default_factory=dict)
    #: The component that performs the join, when it is not a pure function.
    component: Optional[str] = None
    subgoal_id: Optional[str] = None

    def structural_key(self) -> str:
        inner = ",".join(sorted(b.structural_key() for b in self.branches))
        return f"join[{self.merge}]({inner})"

    def children(self) -> list["WorkflowTerm"]:
        return list(self.branches)

    def ensure_ids(self, prefix: str = "t") -> "Join":
        kids = [b.ensure_ids(prefix) for b in self.branches]
        tid = self.term_id or f"join_{_hash_key(self.structural_key())}"
        return self.model_copy(update={"branches": kids, "term_id": tid})


class Verify(TermBase):
    """Attach a verifier to a body. The verifier must be an available evaluator."""

    kind: Literal["verify"] = "verify"
    body: "WorkflowTerm"
    verifier: str = ""
    verifier_config: dict[str, Any] = Field(default_factory=dict)
    #: When true, a failing verification blocks downstream consumption.
    blocking: bool = True

    def structural_key(self) -> str:
        return f"verify[{self.verifier}]({self.body.structural_key()})"

    def children(self) -> list["WorkflowTerm"]:
        return [self.body]

    def ensure_ids(self, prefix: str = "t") -> "Verify":
        body = self.body.ensure_ids(prefix)
        tid = self.term_id or f"ver_{_hash_key(self.structural_key())}"
        return self.model_copy(update={"body": body, "term_id": tid})


class Fallback(TermBase):
    """Run ``alternate`` only if ``primary`` fails its contract checks."""

    kind: Literal["fallback"] = "fallback"
    primary: "WorkflowTerm"
    alternate: "WorkflowTerm"

    def structural_key(self) -> str:
        return f"fallback({self.primary.structural_key()},{self.alternate.structural_key()})"

    def children(self) -> list["WorkflowTerm"]:
        return [self.primary, self.alternate]

    def ensure_ids(self, prefix: str = "t") -> "Fallback":
        tid = self.term_id or f"fb_{_hash_key(self.structural_key())}"
        return self.model_copy(
            update={
                "primary": self.primary.ensure_ids(prefix),
                "alternate": self.alternate.ensure_ids(prefix),
                "term_id": tid,
            }
        )


class HumanGate(TermBase):
    """Suspend for human review under a declared condition."""

    kind: Literal["human_gate"] = "human_gate"
    body: "WorkflowTerm"
    condition: str = ""
    condition_config: dict[str, Any] = Field(default_factory=dict)

    def structural_key(self) -> str:
        return f"human[{self.condition}]({self.body.structural_key()})"

    def children(self) -> list["WorkflowTerm"]:
        return [self.body]

    def ensure_ids(self, prefix: str = "t") -> "HumanGate":
        tid = self.term_id or f"hg_{_hash_key(self.structural_key())}"
        return self.model_copy(update={"body": self.body.ensure_ids(prefix), "term_id": tid})


WorkflowTerm = Annotated[
    Union[Atomic, Sequence, Parallel, Join, Verify, Fallback, HumanGate],
    Field(discriminator="kind"),
]

for _model in (Sequence, Parallel, Join, Verify, Fallback, HumanGate):
    _model.model_rebuild()


# ---------------------------------------------------------------------------
# Traversal helpers
# ---------------------------------------------------------------------------


def iter_terms(term: WorkflowTerm) -> Iterator[WorkflowTerm]:
    """Pre-order traversal over a term tree."""
    yield term
    for child in term.children():
        yield from iter_terms(child)


def find_term(term: WorkflowTerm, term_id: str) -> Optional[WorkflowTerm]:
    for t in iter_terms(term):
        if t.term_id == term_id:
            return t
    return None


def atomics(term: WorkflowTerm) -> list[Atomic]:
    return [t for t in iter_terms(term) if isinstance(t, Atomic)]


def replace_term(
    term: WorkflowTerm, target_id: str, replacement: WorkflowTerm
) -> tuple[WorkflowTerm, bool]:
    """Return a copy of ``term`` with ``target_id`` replaced. Pure.

    Purity is what makes repair transactional: the caller keeps the original
    tree as the rollback point and only commits the new one after shadow
    validation passes.
    """
    if term.term_id == target_id:
        return replacement, True

    if isinstance(term, Sequence):
        kids, changed = _replace_in_list(term.children_terms, target_id, replacement)
        return (term.model_copy(update={"children_terms": kids}), changed)
    if isinstance(term, Parallel):
        kids, changed = _replace_in_list(term.branches, target_id, replacement)
        return (term.model_copy(update={"branches": kids}), changed)
    if isinstance(term, Join):
        kids, changed = _replace_in_list(term.branches, target_id, replacement)
        return (term.model_copy(update={"branches": kids}), changed)
    if isinstance(term, Verify):
        body, changed = replace_term(term.body, target_id, replacement)
        return (term.model_copy(update={"body": body}), changed)
    if isinstance(term, HumanGate):
        body, changed = replace_term(term.body, target_id, replacement)
        return (term.model_copy(update={"body": body}), changed)
    if isinstance(term, Fallback):
        primary, c1 = replace_term(term.primary, target_id, replacement)
        alternate, c2 = replace_term(term.alternate, target_id, replacement)
        return (term.model_copy(update={"primary": primary, "alternate": alternate}), c1 or c2)
    return term, False


def _replace_in_list(
    items: list[WorkflowTerm], target_id: str, replacement: WorkflowTerm
) -> tuple[list[WorkflowTerm], bool]:
    out: list[WorkflowTerm] = []
    changed = False
    for item in items:
        new_item, did = replace_term(item, target_id, replacement)
        changed = changed or did
        out.append(new_item)
    return out, changed


# ---------------------------------------------------------------------------
# Execution graph (lowering target)
# ---------------------------------------------------------------------------


class ExecNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    #: ``component`` | ``merge`` | ``verifier`` | ``human_gate`` | ``adapter``
    role: str
    component: Optional[str] = None
    subgoal_id: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    #: Term this node was lowered from — the link back for repair targeting.
    origin_term: str = ""
    #: Only executed when its guard condition holds (Fallback alternates).
    conditional: bool = False
    guard: Optional[str] = None


class ExecEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    #: Artifact type names flowing along this edge, once resolved.
    artifacts: list[str] = Field(default_factory=list)
    origin_term: str = ""
    #: ``data`` (artifact flow) or ``control`` (ordering only).
    edge_kind: str = "data"

    @property
    def edge_id(self) -> str:
        return f"{self.source}->{self.target}"


class ExecGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[ExecNode] = Field(default_factory=list)
    edges: list[ExecEdge] = Field(default_factory=list)
    entries: list[str] = Field(default_factory=list)
    exits: list[str] = Field(default_factory=list)

    def node(self, node_id: str) -> Optional[ExecNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def predecessors(self, node_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def successors(self, node_id: str) -> list[str]:
        return [e.target for e in self.edges if e.source == node_id]

    def edge(self, source: str, target: str) -> Optional[ExecEdge]:
        for e in self.edges:
            if e.source == source and e.target == target:
                return e
        return None

    @property
    def component_nodes(self) -> list[ExecNode]:
        return [n for n in self.nodes if n.role == "component"]

    def topological_order(self) -> list[str]:
        """Kahn's algorithm. Raises on a cycle — the grammar cannot express one,
        so a cycle here means a patch corrupted the graph."""
        indeg = {n.node_id: 0 for n in self.nodes}
        for e in self.edges:
            if e.target in indeg:
                indeg[e.target] += 1
        ready = sorted([n for n, d in indeg.items() if d == 0])
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for succ in sorted(self.successors(current)):
                indeg[succ] -= 1
                if indeg[succ] == 0:
                    ready.append(succ)
            ready.sort()
        if len(order) != len(self.nodes):
            raise ValueError("execution graph contains a cycle")
        return order


class _Fragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[ExecNode] = Field(default_factory=list)
    edges: list[ExecEdge] = Field(default_factory=list)
    entries: list[str] = Field(default_factory=list)
    exits: list[str] = Field(default_factory=list)


def lower(term: WorkflowTerm) -> ExecGraph:
    """Lower a term tree into an execution graph."""
    term = term.ensure_ids()
    frag = _lower_term(term)
    return ExecGraph(
        nodes=frag.nodes, edges=frag.edges, entries=frag.entries, exits=frag.exits
    )


def _lower_term(term: WorkflowTerm) -> _Fragment:
    if isinstance(term, Atomic):
        node = ExecNode(
            node_id=term.term_id,
            role="component",
            component=term.component,
            subgoal_id=term.subgoal_id,
            config=dict(term.config),
            origin_term=term.term_id,
        )
        return _Fragment(nodes=[node], entries=[node.node_id], exits=[node.node_id])

    if isinstance(term, Sequence):
        nodes: list[ExecNode] = []
        edges: list[ExecEdge] = []
        entries: list[str] = []
        prev_exits: list[str] = []
        for child in term.children_terms:
            frag = _lower_term(child)
            nodes.extend(frag.nodes)
            edges.extend(frag.edges)
            if not entries:
                entries = list(frag.entries)
            for src in prev_exits:
                for dst in frag.entries:
                    edges.append(ExecEdge(source=src, target=dst, origin_term=term.term_id))
            prev_exits = list(frag.exits)
        return _Fragment(nodes=nodes, edges=edges, entries=entries, exits=prev_exits)

    if isinstance(term, Parallel):
        nodes, edges, entries, exits = [], [], [], []
        for branch in term.branches:
            frag = _lower_term(branch)
            nodes.extend(frag.nodes)
            edges.extend(frag.edges)
            entries.extend(frag.entries)
            exits.extend(frag.exits)
        return _Fragment(nodes=nodes, edges=edges, entries=entries, exits=exits)

    if isinstance(term, Join):
        nodes, edges, entries = [], [], []
        branch_exits: list[str] = []
        for branch in term.branches:
            frag = _lower_term(branch)
            nodes.extend(frag.nodes)
            edges.extend(frag.edges)
            entries.extend(frag.entries)
            branch_exits.extend(frag.exits)
        join_node = ExecNode(
            node_id=f"{term.term_id}__merge",
            role="merge",
            component=term.component,
            subgoal_id=term.subgoal_id,
            config={"merge": term.merge, **term.merge_config},
            origin_term=term.term_id,
        )
        nodes.append(join_node)
        for src in branch_exits:
            edges.append(ExecEdge(source=src, target=join_node.node_id, origin_term=term.term_id))
        return _Fragment(nodes=nodes, edges=edges, entries=entries, exits=[join_node.node_id])

    if isinstance(term, Verify):
        frag = _lower_term(term.body)
        verifier_node = ExecNode(
            node_id=f"{term.term_id}__verify",
            role="verifier",
            config={"verifier": term.verifier, "blocking": term.blocking, **term.verifier_config},
            origin_term=term.term_id,
        )
        nodes = list(frag.nodes) + [verifier_node]
        edges = list(frag.edges)
        for src in frag.exits:
            edges.append(
                ExecEdge(source=src, target=verifier_node.node_id, origin_term=term.term_id)
            )
        return _Fragment(
            nodes=nodes, edges=edges, entries=frag.entries, exits=[verifier_node.node_id]
        )

    if isinstance(term, HumanGate):
        frag = _lower_term(term.body)
        gate_node = ExecNode(
            node_id=f"{term.term_id}__human",
            role="human_gate",
            config={"condition": term.condition, **term.condition_config},
            origin_term=term.term_id,
        )
        nodes = list(frag.nodes) + [gate_node]
        edges = list(frag.edges)
        for src in frag.exits:
            edges.append(ExecEdge(source=src, target=gate_node.node_id, origin_term=term.term_id))
        return _Fragment(
            nodes=nodes, edges=edges, entries=frag.entries, exits=[gate_node.node_id]
        )

    if isinstance(term, Fallback):
        primary = _lower_term(term.primary)
        alternate = _lower_term(term.alternate)
        guarded = [
            n.model_copy(update={"conditional": True, "guard": f"failed:{term.primary.term_id}"})
            for n in alternate.nodes
        ]
        edges = list(primary.edges) + list(alternate.edges)
        for src in primary.exits:
            for dst in alternate.entries:
                edges.append(
                    ExecEdge(
                        source=src,
                        target=dst,
                        origin_term=term.term_id,
                        edge_kind="control",
                    )
                )
        return _Fragment(
            nodes=list(primary.nodes) + guarded,
            edges=edges,
            entries=primary.entries,
            exits=primary.exits + alternate.exits,
        )

    raise TypeError(f"unknown workflow term: {type(term).__name__}")


# ---------------------------------------------------------------------------
# Compiled workflow
# ---------------------------------------------------------------------------


class RepairPolicy(BaseModel):
    """Bounds on what repair may do. Explicitly separated by tier."""

    model_config = ConfigDict(extra="forbid")

    max_contract_repairs: int = 3
    max_local_optimizations: int = 2
    max_global_redesigns: int = 1
    #: Same fault class recurring this many times escalates a tier.
    escalate_after_repeats: int = 2
    #: Refuse to act on a diagnosis this uncertain; gather evidence instead.
    max_diagnosis_entropy_bits: float = 1.5
    require_shadow_validation: bool = True
    #: Reject a patch whose shadow run regresses any previously passing check.
    forbid_collateral_regression: bool = True


class CompiledWorkflow(BaseModel):
    """A workflow plus everything needed to audit, run, and repair it."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    task_id: str
    term: WorkflowTerm
    evidence: EvidenceLedger = Field(default_factory=EvidenceLedger)
    repair_policy: RepairPolicy = Field(default_factory=RepairPolicy)
    #: Names of the evaluation contract checks bound to this workflow.
    evaluation_contract: list[str] = Field(default_factory=list)
    #: Static-analysis findings recorded at compile time.
    compile_notes: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)

    def graph(self) -> ExecGraph:
        return lower(self.term)

    @property
    def components(self) -> list[str]:
        return sorted({a.component for a in atomics(self.term)})

    @property
    def n_components(self) -> int:
        return len(atomics(self.term))

    @property
    def n_distinct_components(self) -> int:
        return len(self.components)

    def structural_key(self) -> str:
        return self.term.structural_key()

    def with_term(self, term: WorkflowTerm, *, note: str = "") -> "CompiledWorkflow":
        """Pure structural update — the basis of transactional repair."""
        notes = list(self.compile_notes) + ([note] if note else [])
        return self.model_copy(update={"term": term.ensure_ids(), "compile_notes": notes})


__all__ = [
    "Atomic",
    "Sequence",
    "Parallel",
    "Join",
    "Verify",
    "Fallback",
    "HumanGate",
    "WorkflowTerm",
    "ExecNode",
    "ExecEdge",
    "ExecGraph",
    "lower",
    "iter_terms",
    "find_term",
    "atomics",
    "replace_term",
    "RepairPolicy",
    "CompiledWorkflow",
]
