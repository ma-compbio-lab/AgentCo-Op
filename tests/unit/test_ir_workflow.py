"""Workflow grammar: lowering, traversal, and pure structural replacement."""

from __future__ import annotations

import pytest

from agentcoop.ir.workflow import (
    Atomic,
    CompiledWorkflow,
    Fallback,
    HumanGate,
    Join,
    Parallel,
    Sequence,
    Verify,
    atomics,
    find_term,
    iter_terms,
    lower,
    replace_term,
)


def atom(component: str, subgoal: str) -> Atomic:
    return Atomic(component=component, subgoal_id=subgoal)


class TestIdStability:
    def test_ids_are_deterministic_across_constructions(self) -> None:
        """Recompiling an unchanged workflow must yield identical ids, or patch
        targeting and trace diffing break."""
        build = lambda: Sequence(
            children_terms=[atom("a", "s1"), atom("b", "s2")]
        ).ensure_ids()
        assert build().term_id == build().term_id

    def test_structurally_different_terms_get_different_ids(self) -> None:
        one = Sequence(children_terms=[atom("a", "s1")]).ensure_ids()
        two = Sequence(children_terms=[atom("b", "s1")]).ensure_ids()
        assert one.term_id != two.term_id

    def test_parallel_id_is_order_insensitive(self) -> None:
        """Branch order carries no meaning in a Parallel, so it must not
        change identity."""
        a = Parallel(branches=[atom("x", "s1"), atom("y", "s2")]).ensure_ids()
        b = Parallel(branches=[atom("y", "s2"), atom("x", "s1")]).ensure_ids()
        assert a.term_id == b.term_id

    def test_explicit_id_is_preserved(self) -> None:
        term = Atomic(component="a", subgoal_id="s", term_id="pinned").ensure_ids()
        assert term.term_id == "pinned"


class TestLowering:
    def test_atomic_lowers_to_one_node(self) -> None:
        g = lower(atom("seurat", "markers"))
        assert len(g.nodes) == 1
        assert g.entries == g.exits == [g.nodes[0].node_id]
        assert g.nodes[0].role == "component"

    def test_sequence_wires_exits_to_next_entries(self) -> None:
        g = lower(Sequence(children_terms=[atom("a", "s1"), atom("b", "s2")]))
        assert len(g.edges) == 1
        assert g.edges[0].source.startswith("s1")
        assert g.edges[0].target.startswith("s2")

    def test_parallel_has_no_edges_between_branches(self) -> None:
        g = lower(Parallel(branches=[atom("a", "s1"), atom("b", "s2")]))
        assert g.edges == []
        assert len(g.entries) == 2 and len(g.exits) == 2

    def test_join_adds_a_merge_node_fed_by_every_branch(self) -> None:
        g = lower(
            Join(branches=[atom("a", "s1"), atom("b", "s2")], merge="intersect")
        )
        merge_nodes = [n for n in g.nodes if n.role == "merge"]
        assert len(merge_nodes) == 1
        merge = merge_nodes[0]
        assert merge.config["merge"] == "intersect"
        assert sorted(g.predecessors(merge.node_id)) == sorted(
            [n.node_id for n in g.nodes if n.role == "component"]
        )
        assert g.exits == [merge.node_id]

    def test_verify_appends_a_verifier_after_the_body(self) -> None:
        g = lower(Verify(body=atom("a", "s1"), verifier="db_support"))
        verifier = [n for n in g.nodes if n.role == "verifier"][0]
        assert verifier.config["verifier"] == "db_support"
        assert g.exits == [verifier.node_id]

    def test_human_gate_appends_a_gate_node(self) -> None:
        g = lower(HumanGate(body=atom("a", "s1"), condition="high_risk"))
        gate = [n for n in g.nodes if n.role == "human_gate"][0]
        assert gate.config["condition"] == "high_risk"

    def test_fallback_marks_alternate_nodes_conditional(self) -> None:
        term = Fallback(primary=atom("a", "s1"), alternate=atom("b", "s1")).ensure_ids()
        g = lower(term)
        alternate = [n for n in g.nodes if n.component == "b"][0]
        primary = [n for n in g.nodes if n.component == "a"][0]
        assert alternate.conditional is True
        assert alternate.guard == f"failed:{term.primary.term_id}"
        assert primary.conditional is False

    def test_fallback_edge_is_control_not_data(self) -> None:
        """The alternate does not consume the primary's output; it replaces it."""
        g = lower(Fallback(primary=atom("a", "s1"), alternate=atom("b", "s1")))
        assert all(e.edge_kind == "control" for e in g.edges)

    def test_nested_composition_lowers_and_orders(self) -> None:
        term = Sequence(
            children_terms=[
                atom("load", "s0"),
                Join(branches=[atom("rna", "s1"), atom("atac", "s2")], merge="intersect"),
                Verify(body=atom("annotate", "s3"), verifier="db_support"),
            ]
        )
        g = lower(term)
        order = g.topological_order()
        assert order.index("s0__load") < order.index("s1__rna")
        assert order.index("s1__rna") < order.index([n.node_id for n in g.nodes if n.role == "merge"][0])
        assert order[-1].endswith("__verify")

    def test_topological_order_rejects_a_corrupted_cycle(self) -> None:
        g = lower(Sequence(children_terms=[atom("a", "s1"), atom("b", "s2")]))
        g.edges.append(type(g.edges[0])(source="s2__b", target="s1__a"))
        with pytest.raises(ValueError, match="cycle"):
            g.topological_order()

    def test_every_node_traces_back_to_a_term(self) -> None:
        """Repair targets terms, so a node with no origin is unpatchable."""
        g = lower(
            Sequence(
                children_terms=[
                    Verify(body=atom("a", "s1"), verifier="v"),
                    Join(branches=[atom("b", "s2"), atom("c", "s3")], merge="m"),
                ]
            )
        )
        assert all(n.origin_term for n in g.nodes)


class TestTraversal:
    def _tree(self) -> Sequence:
        return Sequence(
            children_terms=[
                atom("a", "s1"),
                Parallel(branches=[atom("b", "s2"), atom("c", "s3")]),
            ]
        ).ensure_ids()

    def test_iter_terms_is_preorder_and_complete(self) -> None:
        assert len(list(iter_terms(self._tree()))) == 5

    def test_atomics_returns_only_leaves(self) -> None:
        assert {a.component for a in atomics(self._tree())} == {"a", "b", "c"}

    def test_find_term_locates_nested_ids(self) -> None:
        tree = self._tree()
        target = atomics(tree)[2].term_id
        assert find_term(tree, target) is not None

    def test_find_term_returns_none_for_unknown(self) -> None:
        assert find_term(self._tree(), "nope") is None


class TestReplaceTerm:
    def test_replacement_is_pure(self) -> None:
        """The original tree is the rollback point for a repair transaction, so
        replacement must never mutate it."""
        tree = Sequence(children_terms=[atom("a", "s1"), atom("b", "s2")]).ensure_ids()
        target = atomics(tree)[0].term_id
        new_tree, changed = replace_term(tree, target, atom("replacement", "s1").ensure_ids())
        assert changed is True
        assert {a.component for a in atomics(tree)} == {"a", "b"}
        assert {a.component for a in atomics(new_tree)} == {"replacement", "b"}

    def test_replaces_inside_parallel_branches(self) -> None:
        tree = Parallel(branches=[atom("a", "s1"), atom("b", "s2")]).ensure_ids()
        target = atomics(tree)[1].term_id
        new_tree, changed = replace_term(tree, target, atom("z", "s2").ensure_ids())
        assert changed and {a.component for a in atomics(new_tree)} == {"a", "z"}

    def test_replaces_inside_verify_body(self) -> None:
        tree = Verify(body=atom("a", "s1"), verifier="v").ensure_ids()
        target = atomics(tree)[0].term_id
        new_tree, changed = replace_term(tree, target, atom("z", "s1").ensure_ids())
        assert changed and atomics(new_tree)[0].component == "z"

    def test_replaces_inside_both_fallback_arms(self) -> None:
        tree = Fallback(primary=atom("a", "s1"), alternate=atom("b", "s1")).ensure_ids()
        target = tree.alternate.term_id
        new_tree, changed = replace_term(tree, target, atom("z", "s1").ensure_ids())
        assert changed and new_tree.alternate.component == "z"
        assert new_tree.primary.component == "a"

    def test_replacing_the_root_returns_the_replacement(self) -> None:
        tree = atom("a", "s1").ensure_ids()
        new_tree, changed = replace_term(tree, tree.term_id, atom("z", "s1").ensure_ids())
        assert changed and new_tree.component == "z"

    def test_unknown_target_reports_no_change(self) -> None:
        tree = Sequence(children_terms=[atom("a", "s1")]).ensure_ids()
        new_tree, changed = replace_term(tree, "missing", atom("z", "s1"))
        assert changed is False
        assert {a.component for a in atomics(new_tree)} == {"a"}


class TestCompiledWorkflow:
    def test_counts_distinguish_instances_from_distinct_components(self) -> None:
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Sequence(
                children_terms=[atom("a", "s1"), atom("a", "s2"), atom("b", "s3")]
            ).ensure_ids(),
        )
        assert wf.n_components == 3
        assert wf.n_distinct_components == 2
        assert wf.components == ["a", "b"]

    def test_with_term_is_pure_and_records_a_note(self) -> None:
        wf = CompiledWorkflow(workflow_id="w", task_id="t", term=atom("a", "s1").ensure_ids())
        patched = wf.with_term(atom("b", "s1").ensure_ids(), note="replaced a with b")
        assert wf.components == ["a"]
        assert patched.components == ["b"]
        assert patched.compile_notes == ["replaced a with b"]

    def test_graph_round_trips_through_serialization(self) -> None:
        """Workflows are persisted between compile, run, and repair."""
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Sequence(
                children_terms=[
                    atom("a", "s1"),
                    Join(branches=[atom("b", "s2"), atom("c", "s3")], merge="m"),
                ]
            ).ensure_ids(),
        )
        restored = CompiledWorkflow.model_validate_json(wf.model_dump_json())
        assert restored.structural_key() == wf.structural_key()
        assert len(restored.graph().nodes) == len(wf.graph().nodes)
