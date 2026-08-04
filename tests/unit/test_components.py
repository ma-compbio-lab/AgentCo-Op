"""The invocation boundary — the one layer that touches the outside world.

Everything above it reasons about components only through ``Invocation`` and
``InvocationResult``, so these tests are mostly about what an adapter is *not*
allowed to do: smuggle information out of band, claim facets it never saw,
report success on an empty result without saying so, or declare itself
shadow-safe when re-running it would have real consequences.

Each adapter's `plan()` is pure and asserted on without executing anything, so
the container and coding-agent paths are testable with neither docker nor a
coding-agent CLI installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcoop.components.base import (
    EMPTY_FIELD_PREFIX,
    EMPTY_PAYLOAD_NOTE,
    FACET_CONFLICT_PREFIX,
    AdapterRegistry,
    Invocation,
    TemplateError,
    behavior_of,
    draft_artifact,
    empty_field_paths,
    error_line,
    errors_of_class,
    finalize_outputs,
    make_artifact,
    parse_error_line,
    payload_is_empty,
    render_argv,
    zero_clock,
)
from agentcoop.components.coding_agent import CodingAgentAdapter
from agentcoop.components.container import ContainerAdapter, docker_available
from agentcoop.components.python_fn import PythonFunctionAdapter
from agentcoop.components.subprocess_adapter import SubprocessAdapter
from agentcoop.ir.faults import FaultClass


def inv(**kw) -> Invocation:
    kw.setdefault("component", "c")
    return Invocation(**kw)


# ---------------------------------------------------------------------------
# Fault-shaped errors
# ---------------------------------------------------------------------------


class TestErrorLines:
    def test_round_trip(self) -> None:
        line = error_line(FaultClass.ENVIRONMENT, "docker is missing")
        assert parse_error_line(line) == (FaultClass.ENVIRONMENT, "docker is missing")

    def test_untagged_text_is_returned_verbatim(self) -> None:
        """Diagnosis must not regex-guess over prose it did not produce."""
        assert parse_error_line("plain failure") == (None, "plain failure")

    def test_unknown_tag_does_not_crash(self) -> None:
        assert parse_error_line("[not_a_fault] x") == (None, "[not_a_fault] x")

    def test_filtering_by_class(self) -> None:
        lines = [
            error_line(FaultClass.ENVIRONMENT, "a"),
            error_line(FaultClass.TOOL_FAILURE, "b"),
        ]
        assert errors_of_class(lines, FaultClass.TOOL_FAILURE) == ["b"]


# ---------------------------------------------------------------------------
# Emptiness
# ---------------------------------------------------------------------------


class TestEmptiness:
    @pytest.mark.parametrize("value", [None, "", [], {}, set()])
    def test_empty_values(self, value) -> None:
        assert payload_is_empty(value)

    @pytest.mark.parametrize("value", [0, False, "x", [0], {"a": 1}])
    def test_non_empty_values(self, value) -> None:
        """0 and False are data, not absence."""
        assert not payload_is_empty(value)

    def test_the_healthy_envelope_with_nothing_in_it_is_named(self) -> None:
        """{"status": "ok", "genes": []} is the canonical silent failure."""
        assert empty_field_paths({"status": "ok", "genes": []}) == ["genes"]

    def test_nested_paths_are_reported(self) -> None:
        paths = empty_field_paths({"a": {"b": [], "c": 1}})
        assert paths == ["a.b"]

    def test_paths_are_sorted_for_determinism(self) -> None:
        paths = empty_field_paths({"z": [], "a": [], "m": []})
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# Observed vs declared facets
# ---------------------------------------------------------------------------


class TestFacetObservation:
    def test_declared_facets_reach_the_artifact_but_not_the_observation(self) -> None:
        """A component that emits bare artifacts must stay UNDERSPECIFIED.

        Declared facets come from static config, so crediting them as observed
        would let a card's own claims certify themselves.
        """
        out = finalize_outputs(
            {"gene_set": draft_artifact(type_name="gene_set", payload={"genes": ["A"]})},
            declared_facets={"gene_set": {"namespace": "HGNC"}},
        )
        assert out.outputs["gene_set"].facets["namespace"] == "HGNC"
        assert "gene_set" not in out.observed_facets

    def test_emitted_facets_are_observed(self) -> None:
        out = finalize_outputs(
            {
                "gene_set": draft_artifact(
                    type_name="gene_set", payload={"genes": ["A"]}, facets={"namespace": "HGNC"}
                )
            }
        )
        assert out.observed_facets["gene_set"] == {"namespace": "HGNC"}

    def test_an_observer_overrides_a_declaration_and_records_the_conflict(self) -> None:
        """'The card says HGNC and the tool emits Ensembl' must be reportable."""
        out = finalize_outputs(
            {"gene_set": draft_artifact(type_name="gene_set", payload={"genes": ["ENSG1"]})},
            declared_facets={"gene_set": {"namespace": "HGNC"}},
            observers={"gene_set": lambda a: {"namespace": "ENSEMBL"}},
        )
        art = out.outputs["gene_set"]
        assert art.facets["namespace"] == "ENSEMBL"
        assert any(n.startswith(FACET_CONFLICT_PREFIX) for n in art.notes)
        assert out.notes and "declared=HGNC observed=ENSEMBL" in out.notes[0]

    def test_emptiness_is_annotated_at_the_boundary(self) -> None:
        out = finalize_outputs(
            {
                "empty": draft_artifact(type_name="empty", payload={}),
                "hollow": draft_artifact(type_name="hollow", payload={"ok": True, "genes": []}),
            }
        )
        assert EMPTY_PAYLOAD_NOTE in out.outputs["empty"].notes
        assert f"{EMPTY_FIELD_PREFIX}genes" in out.outputs["hollow"].notes

    def test_content_hash_covers_facets(self) -> None:
        """Two artifacts with the same payload but different meaning differ."""
        a = make_artifact(type_name="t", payload={"x": 1}, facets={"organism": "human"})
        b = make_artifact(type_name="t", payload={"x": 1}, facets={"organism": "mouse"})
        assert a.content_hash != b.content_hash

    def test_identity_is_stable_across_runs(self) -> None:
        a = make_artifact(type_name="t", payload={"x": 1}, producer="n")
        b = make_artifact(type_name="t", payload={"x": 1}, producer="n")
        assert a.artifact_id == b.artifact_id


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------


class TestTemplating:
    def test_placeholders_resolve(self) -> None:
        art = make_artifact(type_name="gene_set", payload={"genes": ["A"]}, facets={"organism": "human"})
        argv = render_argv(
            ["run", "--in", "{in:gene_set}", "--org", "{facet:gene_set.organism}", "--seed", "{seed}"],
            inv(inputs={"gene_set": art}, seed=7),
        )
        assert argv == ["run", "--in", '{"genes": ["A"]}', "--org", "human", "--seed", "7"]

    def test_an_unknown_placeholder_raises_rather_than_interpolating(self) -> None:
        """Silently formatting a bad placeholder into a command line is worse."""
        with pytest.raises(TemplateError):
            render_argv(["run", "{nope}"], inv())

    def test_a_missing_config_key_is_named(self) -> None:
        with pytest.raises(TemplateError, match="missing config key 'k'"):
            render_argv(["{cfg:k}"], inv())

    def test_braces_can_be_escaped(self) -> None:
        assert render_argv(["{{literal}}"], inv()) == ["{literal}"]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    def test_iteration_order_is_sorted_not_registration_order(self) -> None:
        registry = AdapterRegistry(
            [PythonFunctionAdapter(n, lambda i: {}, output_type="t") for n in ("z", "a", "m")]
        )
        assert registry.names() == ["a", "m", "z"]

    def test_an_unregistered_name_lists_what_is_available(self) -> None:
        registry = AdapterRegistry([PythonFunctionAdapter("a", lambda i: {}, output_type="t")])
        with pytest.raises(KeyError, match="registered: a"):
            registry.get("b")

    def test_a_non_adapter_is_rejected(self) -> None:
        class NotAnAdapter:
            name = "x"

        with pytest.raises(TypeError):
            AdapterRegistry([NotAnAdapter()])


# ---------------------------------------------------------------------------
# PythonFunctionAdapter
# ---------------------------------------------------------------------------


class TestPythonFunctionAdapter:
    async def test_a_bare_return_becomes_a_typed_artifact(self) -> None:
        adapter = PythonFunctionAdapter(
            "f", lambda i: {"genes": ["A"]}, output_type="gene_set", clock=zero_clock
        )
        result = await adapter.invoke(inv(component="f"))
        assert result.ok
        assert result.outputs["gene_set"].payload == {"genes": ["A"]}

    async def test_an_exception_becomes_a_classed_failure_not_a_traceback(self) -> None:
        def boom(i):
            raise ModuleNotFoundError("no module named 'scanpy'")

        adapter = PythonFunctionAdapter("f", boom, output_type="t", clock=zero_clock)
        result = await adapter.invoke(inv(component="f"))
        assert not result.ok
        assert result.has_fault(FaultClass.ENVIRONMENT)

    async def test_a_value_error_is_a_contract_fault_not_an_environment_one(self) -> None:
        def bad(i):
            raise ValueError("gene set is malformed")

        adapter = PythonFunctionAdapter("f", bad, output_type="t", clock=zero_clock)
        result = await adapter.invoke(inv(component="f"))
        assert not result.ok
        assert not result.has_fault(FaultClass.ENVIRONMENT)

    async def test_an_empty_return_is_flagged_rather_than_passed_on(self) -> None:
        adapter = PythonFunctionAdapter(
            "f", lambda i: {"genes": []}, output_type="gene_set", clock=zero_clock
        )
        result = await adapter.invoke(inv(component="f"))
        assert result.ok, "the component did succeed by its own lights"
        assert f"{EMPTY_FIELD_PREFIX}genes" in result.outputs["gene_set"].notes

    async def test_lineage_is_recorded(self) -> None:
        art = make_artifact(type_name="src", payload={"x": 1}, producer="upstream")
        adapter = PythonFunctionAdapter("f", lambda i: {"y": 2}, output_type="out", clock=zero_clock)
        result = await adapter.invoke(inv(component="f", inputs={"src": art}))
        assert result.outputs["out"].derived_from == [art.artifact_id]


# ---------------------------------------------------------------------------
# SubprocessAdapter
# ---------------------------------------------------------------------------


class TestSubprocessAdapter:
    def test_it_does_not_claim_determinism_by_default(self) -> None:
        """An arbitrary external command is assumed non-deterministic."""
        contract = behavior_of(SubprocessAdapter("s", ["true"]))
        assert contract is not None
        assert contract.deterministic is False

    async def test_a_missing_workdir_is_a_named_environment_failure(self) -> None:
        adapter = SubprocessAdapter("s", ["true"], clock=zero_clock)
        result = await adapter.invoke(inv(component="s"))
        assert not result.ok
        assert result.has_fault(FaultClass.ENVIRONMENT)

    async def test_a_bad_template_fails_before_anything_runs(self, tmp_path: Path) -> None:
        adapter = SubprocessAdapter("s", ["echo", "{cfg:absent}"], clock=zero_clock)
        result = await adapter.invoke(inv(component="s", workdir=tmp_path))
        assert not result.ok
        assert result.has_fault(FaultClass.CONFIGURATION)

    async def test_a_real_command_produces_a_declared_output(self, tmp_path: Path) -> None:
        adapter = SubprocessAdapter(
            "s",
            ["python", "-c", "import json,sys; open(sys.argv[1],'w').write(json.dumps({'n': 3}))", "{out:result}"],
            outputs={"result": {"source": "file", "path": "result.json", "format": "json"}},
            clock=zero_clock,
        )
        result = await adapter.invoke(inv(component="s", workdir=tmp_path))
        assert result.ok, result.errors
        assert result.outputs["result"].payload == {"n": 3}

    async def test_a_nonzero_exit_is_reported_with_its_code(self, tmp_path: Path) -> None:
        adapter = SubprocessAdapter("s", ["python", "-c", "raise SystemExit(3)"], clock=zero_clock)
        result = await adapter.invoke(inv(component="s", workdir=tmp_path))
        assert not result.ok
        assert result.exit_code == 3

    async def test_the_exact_argv_is_logged_for_replay(self, tmp_path: Path) -> None:
        adapter = SubprocessAdapter("s", ["python", "-c", "pass"], clock=zero_clock)
        result = await adapter.invoke(inv(component="s", workdir=tmp_path))
        assert "argv=" in result.logs


# ---------------------------------------------------------------------------
# ContainerAdapter — planned, never run
# ---------------------------------------------------------------------------


class TestContainerAdapter:
    def test_the_command_line_is_inspectable_without_docker(self, tmp_path: Path) -> None:
        adapter = ContainerAdapter("c", "img:1", ["analyse"], network=False, memory_gb=2)
        argv = adapter.docker_argv(inv(component="c", workdir=tmp_path), tmp_path)
        assert argv[:3] == ["docker", "run", "--rm"]
        assert "img:1" in argv and "analyse" in argv

    def test_the_network_is_off_unless_asked_for(self) -> None:
        """Isolation has to be the default, or the contract means nothing."""
        argv = ContainerAdapter("c", "img:1").docker_argv(
            inv(component="c", workdir=Path("/tmp")), Path("/tmp")
        )
        assert "--network" in argv and "none" in argv

    def test_the_network_flag_appears_when_requested(self) -> None:
        argv = ContainerAdapter("c", "img:1", network=True).docker_argv(
            inv(component="c", workdir=Path("/tmp")), Path("/tmp")
        )
        assert "none" not in argv

    def test_a_memory_limit_is_passed_through(self) -> None:
        argv = ContainerAdapter("c", "img:1", memory_gb=4).docker_argv(
            inv(component="c", workdir=Path("/tmp")), Path("/tmp")
        )
        assert "--memory" in argv

    async def test_a_missing_runtime_is_an_environment_fault_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        adapter = ContainerAdapter("c", "img:1", docker_binary="definitely-not-on-path")
        result = await adapter.invoke(inv(component="c", workdir=tmp_path))
        assert not result.ok
        assert result.has_fault(FaultClass.ENVIRONMENT)
        assert "argv=" in result.logs, "the command that would have run is still recorded"

    def test_docker_available_is_a_path_lookup(self) -> None:
        assert docker_available("definitely-not-on-path") is False


# ---------------------------------------------------------------------------
# CodingAgentAdapter — the "why not just use Codex" arm, as a node
# ---------------------------------------------------------------------------


class TestCodingAgentAdapter:
    def test_it_is_not_shadow_safe(self) -> None:
        """Re-running a repo-editing agent to see whether a guess was right is
        not a validation strategy, and ShadowValidator relies on this."""
        contract = behavior_of(CodingAgentAdapter("a", executor="codex"))
        assert contract is not None
        assert contract.shadow_safe is False
        assert contract.deterministic is False
        assert contract.side_effects

    def test_an_unknown_executor_is_rejected_with_the_known_list(self) -> None:
        with pytest.raises(ValueError, match="known:"):
            CodingAgentAdapter("a", executor="not-a-real-cli")

    def test_an_explicit_argv_bypasses_the_preset(self) -> None:
        adapter = CodingAgentAdapter("a", executor="whatever", argv=["mycli", "{prompt}"])
        assert adapter.argv_template == ["mycli", "{prompt}"]

    def test_the_plan_is_inspectable_without_the_cli_installed(self, tmp_path: Path) -> None:
        adapter = CodingAgentAdapter(
            "a", executor="codex", prompt_template="do {cfg:what} in {workdir}"
        )
        plan = adapter.plan(
            inv(component="a", workdir=tmp_path, config={"what": "the analysis"})
        )
        assert "do the analysis in" in plan.prompt
        assert plan.cwd == tmp_path
        assert plan.argv

    async def test_a_missing_cli_is_an_environment_fault(self, tmp_path: Path) -> None:
        adapter = CodingAgentAdapter(
            "a", executor="codex", binary="definitely-not-on-path", clock=zero_clock
        )
        result = await adapter.invoke(
            inv(component="a", workdir=tmp_path, config={"instruction": "x"})
        )
        assert not result.ok
        assert result.has_fault(FaultClass.ENVIRONMENT)


def test_every_kept_adapter_declares_its_own_behaviour() -> None:
    """An adapter that says nothing must never be assumed deterministic.

    `behavior_of` returns None rather than a permissive default, so a silent
    adapter would be treated as unknown — but the four adapters shipped here
    are expected to answer for themselves.
    """
    adapters = [
        PythonFunctionAdapter("p", lambda i: {}, output_type="t"),
        SubprocessAdapter("s", ["true"]),
        ContainerAdapter("c", "img:1"),
        CodingAgentAdapter("a", executor="codex"),
    ]
    assert all(behavior_of(a) is not None for a in adapters)
