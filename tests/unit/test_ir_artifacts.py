"""Artifact typing, facet compatibility, and lineage."""

from __future__ import annotations

import pytest

from agentcoop.ir.artifacts import (
    Artifact,
    ArtifactType,
    Compatibility,
    Converter,
    TypeRegistry,
    build_lineage_index,
    lineage_of,
    validate_payload,
)


GENE_SET_SCHEMA = {
    "type": "object",
    "required": ["genes"],
    "properties": {"genes": {"type": "array"}, "n": {"type": "integer"}},
}


def gene_set(**facets: str) -> ArtifactType:
    return ArtifactType(
        name="gene_set",
        json_schema=GENE_SET_SCHEMA,
        required_facets=["namespace", "organism"],
        facets=dict(facets),
    )


class TestFacetCompatibility:
    def test_identical_contracts_are_compatible(self) -> None:
        reg = TypeRegistry()
        producer = gene_set(namespace="HGNC", organism="human")
        consumer = gene_set(namespace="HGNC", organism="human")
        assert reg.check_compatibility(producer, consumer).verdict is Compatibility.COMPATIBLE

    def test_undeclared_required_facet_is_underspecified_not_compatible(self) -> None:
        """The core anti-assumption rule.

        A producer that never says which identifier namespace it emits has not
        earned a compatibility verdict, even though nothing observably clashes.
        """
        reg = TypeRegistry()
        producer = gene_set(organism="human")  # namespace never declared
        consumer = gene_set(namespace="HGNC", organism="human")
        report = reg.check_compatibility(producer, consumer)
        assert report.verdict is Compatibility.UNDERSPECIFIED
        assert "namespace" in report.missing_facets
        assert not report.ok

    def test_conflicting_facet_without_converter_is_incompatible(self) -> None:
        reg = TypeRegistry()
        producer = gene_set(namespace="ENSEMBL", organism="human")
        consumer = gene_set(namespace="HGNC", organism="human")
        report = reg.check_compatibility(producer, consumer)
        assert report.verdict is Compatibility.INCOMPATIBLE
        assert report.conflicting_facets["namespace"] == ("ENSEMBL", "HGNC")

    def test_conflicting_facet_with_registered_converter_needs_adapter(self) -> None:
        reg = TypeRegistry()
        reg.register_converter(
            Converter(
                name="ensembl_to_hgnc",
                type_name="gene_set",
                from_facets={"namespace": "ENSEMBL"},
                to_facets={"namespace": "HGNC"},
                expected_coverage=0.92,
            )
        )
        report = reg.check_compatibility(
            gene_set(namespace="ENSEMBL", organism="human"),
            gene_set(namespace="HGNC", organism="human"),
        )
        assert report.verdict is Compatibility.NEEDS_ADAPTER
        assert report.adapter == "ensembl_to_hgnc"

    def test_organism_mismatch_is_caught_independently_of_namespace(self) -> None:
        reg = TypeRegistry()
        report = reg.check_compatibility(
            gene_set(namespace="HGNC", organism="mouse"),
            gene_set(namespace="HGNC", organism="human"),
        )
        assert report.verdict is Compatibility.INCOMPATIBLE
        assert "organism" in report.conflicting_facets

    def test_type_name_mismatch_short_circuits(self) -> None:
        reg = TypeRegistry()
        a = ArtifactType(name="de_results", json_schema={})
        b = ArtifactType(name="gene_set", json_schema={})
        assert reg.check_compatibility(a, b).verdict is Compatibility.INCOMPATIBLE

    def test_converter_not_applied_when_disallowed(self) -> None:
        reg = TypeRegistry()
        reg.register_converter(
            Converter(
                name="ensembl_to_hgnc",
                type_name="gene_set",
                from_facets={"namespace": "ENSEMBL"},
                to_facets={"namespace": "HGNC"},
            )
        )
        report = reg.check_compatibility(
            gene_set(namespace="ENSEMBL", organism="human"),
            gene_set(namespace="HGNC", organism="human"),
            allow_adapter=False,
        )
        assert report.verdict is Compatibility.INCOMPATIBLE


class TestStructuralCompatibility:
    def test_missing_required_property_is_incompatible(self) -> None:
        reg = TypeRegistry()
        producer = ArtifactType(
            name="t", json_schema={"properties": {"other": {"type": "string"}}}
        )
        consumer = ArtifactType(
            name="t",
            json_schema={"required": ["genes"], "properties": {"genes": {"type": "array"}}},
        )
        report = reg.check_compatibility(producer, consumer)
        assert report.verdict is Compatibility.INCOMPATIBLE
        assert any("genes" in p for p in report.schema_problems)

    def test_property_type_clash_is_incompatible(self) -> None:
        reg = TypeRegistry()
        producer = ArtifactType(name="t", json_schema={"properties": {"n": {"type": "string"}}})
        consumer = ArtifactType(name="t", json_schema={"properties": {"n": {"type": "integer"}}})
        assert reg.check_compatibility(producer, consumer).verdict is Compatibility.INCOMPATIBLE

    def test_structural_clash_beats_matching_facets(self) -> None:
        """A schema clash must not be masked by agreeable facets."""
        reg = TypeRegistry()
        producer = ArtifactType(
            name="gene_set",
            json_schema={"properties": {"genes": {"type": "string"}}},
            required_facets=["namespace"],
            facets={"namespace": "HGNC"},
        )
        consumer = ArtifactType(
            name="gene_set",
            json_schema={"required": ["genes"], "properties": {"genes": {"type": "array"}}},
            required_facets=["namespace"],
            facets={"namespace": "HGNC"},
        )
        assert reg.check_compatibility(producer, consumer).verdict is Compatibility.INCOMPATIBLE


class TestPayloadValidation:
    def test_valid_payload_has_no_problems(self) -> None:
        assert validate_payload({"genes": ["TP53"], "n": 1}, gene_set()) == []

    def test_missing_required_key_reported(self) -> None:
        problems = validate_payload({"n": 1}, gene_set())
        assert any("genes" in p for p in problems)

    def test_bool_is_not_accepted_as_integer(self) -> None:
        """bool subclasses int in Python; a count of ``True`` is a real bug."""
        problems = validate_payload({"genes": [], "n": True}, gene_set())
        assert any("boolean" in p for p in problems)

    def test_non_object_payload_for_object_schema(self) -> None:
        problems = validate_payload(["TP53"], gene_set())
        assert len(problems) == 1
        assert "expected object" in problems[0]


class TestLineage:
    def _chain(self) -> dict[str, Artifact]:
        raw = Artifact(artifact_id="a0", type_name="matrix", producer="load")
        de = Artifact(artifact_id="a1", type_name="de_results", producer="de", derived_from=["a0"])
        markers = Artifact(
            artifact_id="a2", type_name="gene_set", producer="select", derived_from=["a1"]
        )
        report = Artifact(
            artifact_id="a3", type_name="report", producer="interpret", derived_from=["a2"]
        )
        return build_lineage_index([raw, de, markers, report])

    def test_lineage_walks_back_to_root_nearest_first(self) -> None:
        assert lineage_of("a3", self._chain()) == ["a3", "a2", "a1", "a0"]

    def test_lineage_of_root_is_just_itself(self) -> None:
        assert lineage_of("a0", self._chain()) == ["a0"]

    def test_diamond_lineage_visits_each_ancestor_once(self) -> None:
        root = Artifact(artifact_id="r", type_name="m", producer="p")
        left = Artifact(artifact_id="l", type_name="m", producer="p", derived_from=["r"])
        right = Artifact(artifact_id="q", type_name="m", producer="p", derived_from=["r"])
        joined = Artifact(
            artifact_id="j", type_name="m", producer="p", derived_from=["l", "q"]
        )
        order = lineage_of("j", build_lineage_index([root, left, right, joined]))
        assert order.count("r") == 1
        assert set(order) == {"j", "l", "q", "r"}

    def test_corrupted_cycle_terminates(self) -> None:
        a = Artifact(artifact_id="a", type_name="m", producer="p", derived_from=["b"])
        b = Artifact(artifact_id="b", type_name="m", producer="p", derived_from=["a"])
        assert set(lineage_of("a", build_lineage_index([a, b]))) == {"a", "b"}

    def test_missing_ancestor_does_not_raise(self) -> None:
        orphan = Artifact(artifact_id="x", type_name="m", producer="p", derived_from=["gone"])
        assert lineage_of("x", build_lineage_index([orphan])) == ["x", "gone"]


class TestArtifactHashing:
    def test_hash_is_stable_and_content_sensitive(self) -> None:
        a = Artifact(artifact_id="a", type_name="gene_set", payload={"genes": ["TP53"]})
        b = Artifact(artifact_id="b", type_name="gene_set", payload={"genes": ["TP53"]})
        c = Artifact(artifact_id="c", type_name="gene_set", payload={"genes": ["MYC"]})
        assert a.compute_hash() == b.compute_hash()
        assert a.compute_hash() != c.compute_hash()

    def test_facets_participate_in_the_hash(self) -> None:
        """Same genes under a different namespace are not the same artifact."""
        a = Artifact(
            artifact_id="a", type_name="gene_set", payload={"genes": ["X"]},
            facets={"namespace": "HGNC"},
        )
        b = Artifact(
            artifact_id="b", type_name="gene_set", payload={"genes": ["X"]},
            facets={"namespace": "ENSEMBL"},
        )
        assert a.compute_hash() != b.compute_hash()

    def test_finalize_is_idempotent(self) -> None:
        a = Artifact(artifact_id="a", type_name="t", payload={"x": 1}).finalize()
        first = a.content_hash
        assert a.finalize().content_hash == first

    def test_unserializable_payload_still_hashes(self) -> None:
        a = Artifact(artifact_id="a", type_name="t", payload=object())
        assert len(a.compute_hash()) == 16


class TestTypeRegistry:
    def test_require_raises_for_unknown_type(self) -> None:
        with pytest.raises(KeyError):
            TypeRegistry().require("nope")

    def test_names_are_sorted(self) -> None:
        reg = TypeRegistry()
        reg.register_type(ArtifactType(name="zeta"))
        reg.register_type(ArtifactType(name="alpha"))
        assert reg.names() == ["alpha", "zeta"]

    def test_with_facets_does_not_mutate_original(self) -> None:
        base = gene_set(organism="human")
        derived = base.with_facets(namespace="HGNC")
        assert "namespace" not in base.facets
        assert derived.facets["namespace"] == "HGNC"
        assert derived.facets["organism"] == "human"
