"""Run an external command and collect its declared outputs.

Also the home of the process plumbing that :mod:`container` and
:mod:`coding_agent` reuse: input materialization, argv rendering, bounded
execution, and output collection.

The collection step is where this adapter earns its keep. A subprocess that
exits 0 having written nothing is not a success — it is the silent failure
mode that makes "the workflow ran to completion" a meaningless statement. So
a declared output that never appeared is reported as an
``ARTIFACT_CONTRACT``-classed error *even when the exit code is zero*, and the
message says exactly that, because the distinction between "crashed" and
"succeeded without producing anything" is the one a diagnoser needs and the
one an exit code destroys.

Nothing here goes through a shell. Commands are argv lists rendered from
templates with a resolver that raises on an unknown placeholder, so a typo in
a command template becomes a CONFIGURATION fault at invocation time rather
than a mysteriously mangled command line.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping, NamedTuple, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.faults import FaultClass

from agentcoop.components.base import (
    Clock,
    FacetObserver,
    Invocation,
    InvocationResult,
    TemplateError,
    draft_artifact,
    ensure_workdir,
    error_line,
    failure_result,
    file_digest,
    finalize_outputs,
    input_ids,
    perf_clock,
    render_argv,
)


class OutputSpec(BaseModel):
    """Where one declared output artifact comes from and how to read it."""

    model_config = ConfigDict(extra="forbid")

    #: Path relative to the workdir. Ignored for stdout/stderr sources.
    path: str = ""
    source: Literal["file", "stdout", "stderr"] = "file"
    format: Literal["json", "text", "bytes"] = "json"
    #: Statically declared facets. Applied to the artifact, never reported as
    #: observed — the process wrote bytes, it did not attest to a namespace.
    facets: dict[str, str] = Field(default_factory=dict)
    #: A missing optional output is recorded, not treated as a failure.
    required: bool = True


class ProcessOutcome(NamedTuple):
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    #: Set when the binary itself could not be launched.
    launch_error: Optional[str] = None


async def run_process(
    argv: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout_s: float = 300.0,
    stdin: Optional[str] = None,
) -> ProcessOutcome:
    """Run ``argv`` to completion or kill it at ``timeout_s``.

    Never uses ``shell=True``: a rendered template must not be re-parsed by a
    shell, or a facet value containing a semicolon becomes code execution.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        return ProcessOutcome(None, "", "", False, f"{type(exc).__name__}: {exc}")

    payload = stdin.encode("utf-8") if stdin is not None else None
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout=timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        try:
            await proc.wait()
        except ProcessLookupError:  # pragma: no cover - race on fast exit
            pass
        return ProcessOutcome(proc.returncode, "", "", True)

    return ProcessOutcome(
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
        False,
    )


def build_env(
    inv: Invocation,
    *,
    overrides: Optional[Mapping[str, str]] = None,
    inherit: bool = True,
) -> dict[str, str]:
    """Child environment, with the seed propagated for reproducibility.

    ``PYTHONHASHSEED`` matters more than it looks: a child Python process that
    iterates a set and writes the result will otherwise emit different bytes
    on every run, and the determinism probe would blame the component for what
    is really an unpinned interpreter.
    """
    env = dict(os.environ) if inherit else {}
    env["PYTHONHASHSEED"] = str(inv.seed)
    env["AGENTCOOP_SEED"] = str(inv.seed)
    env.update({str(k): str(v) for k, v in (overrides or {}).items()})
    return env


def materialize_inputs(
    inv: Invocation, workdir: Path, *, subdir: str = "inputs"
) -> dict[str, str]:
    """Write in-memory input artifacts to disk so a process can read them.

    File names are derived from the artifact *type*, never from a counter or a
    timestamp, so the same invocation produces the same paths on every run and
    a command line stays diffable across runs.
    """
    paths: dict[str, str] = {}
    target_dir = workdir / subdir
    for type_name in sorted(inv.inputs):
        art = inv.inputs[type_name]
        if art.path:
            paths[type_name] = str(art.path)
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{_safe_name(type_name)}.json"
        target.write_text(
            json.dumps(art.payload, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
        paths[type_name] = str(target)
    return paths


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)


def output_paths_for(
    specs: Mapping[str, OutputSpec], workdir: Path, *, root: Optional[str] = None
) -> dict[str, str]:
    """Map each file-sourced output to the path the component must write.

    ``root`` overrides the path prefix used when rendering the command line —
    a container sees ``/work/out.json`` where the host sees a temp directory.
    """
    paths: dict[str, str] = {}
    for type_name in sorted(specs):
        spec = specs[type_name]
        if spec.source != "file" or not spec.path:
            continue
        if root is not None:
            paths[type_name] = f"{root.rstrip('/')}/{spec.path.lstrip('/')}"
        else:
            paths[type_name] = str(workdir / spec.path)
    return paths


def collect_outputs(
    specs: Mapping[str, OutputSpec],
    *,
    workdir: Path,
    outcome: ProcessOutcome,
) -> tuple[dict[str, Artifact], dict[str, dict[str, str]], list[str]]:
    """Read declared outputs off disk / stdout.

    Returns ``(artifacts, declared_facets, errors)``. A required output that
    the process did not produce yields an error whose wording distinguishes a
    crash from a silent no-op, because those demand different repairs.
    """
    artifacts: dict[str, Artifact] = {}
    declared: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for type_name in sorted(specs):
        spec = specs[type_name]
        declared[type_name] = dict(spec.facets)

        if spec.source in ("stdout", "stderr"):
            text = outcome.stdout if spec.source == "stdout" else outcome.stderr
            payload, parse_error = _parse(text, spec.format, f"{spec.source} of '{type_name}'")
            if parse_error:
                errors.append(error_line(FaultClass.ARTIFACT_CONTRACT, parse_error))
                continue
            artifacts[type_name] = draft_artifact(type_name=type_name, payload=payload)
            continue

        if not spec.path:
            errors.append(
                error_line(
                    FaultClass.CONFIGURATION,
                    f"output '{type_name}' declares source='file' but no path",
                )
            )
            continue

        target = workdir / spec.path
        if not target.exists():
            if not spec.required:
                continue
            if outcome.exit_code == 0:
                errors.append(
                    error_line(
                        FaultClass.ARTIFACT_CONTRACT,
                        f"declared output '{type_name}' is missing at '{spec.path}' "
                        "even though the process exited 0 — silent success, not success",
                    )
                )
            else:
                errors.append(
                    error_line(
                        FaultClass.TOOL_FAILURE,
                        f"declared output '{type_name}' is missing at '{spec.path}' "
                        f"(process exited {outcome.exit_code})",
                    )
                )
            continue

        raw = target.read_bytes()
        if spec.format == "bytes":
            artifacts[type_name] = draft_artifact(
                type_name=type_name,
                payload=None,
                path=str(target),
                content_hash=file_digest(raw),
            )
            continue

        payload, parse_error = _parse(
            raw.decode("utf-8", errors="replace"), spec.format, f"file '{spec.path}'"
        )
        if parse_error:
            errors.append(error_line(FaultClass.ARTIFACT_CONTRACT, parse_error))
            continue
        artifacts[type_name] = draft_artifact(
            type_name=type_name, payload=payload, path=str(target)
        )

    return artifacts, declared, errors


def _parse(text: str, fmt: str, where: str) -> tuple[Any, Optional[str]]:
    if fmt != "json":
        return text, None
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{where} is not valid JSON: {exc}"


def parse_output_specs(raw: Mapping[str, Any]) -> dict[str, OutputSpec]:
    """Accept either ``{"T": "out.json"}`` shorthand or full specs."""
    specs: dict[str, OutputSpec] = {}
    for type_name, value in raw.items():
        if isinstance(value, OutputSpec):
            specs[str(type_name)] = value
        elif isinstance(value, str):
            specs[str(type_name)] = OutputSpec(path=value)
        else:
            specs[str(type_name)] = OutputSpec.model_validate(value)
    return specs


class SubprocessAdapter:
    """Run a command line, enforce a timeout, collect declared outputs."""

    def __init__(
        self,
        name: str,
        argv: Sequence[str],
        *,
        outputs: Optional[Mapping[str, Any]] = None,
        env: Optional[Mapping[str, str]] = None,
        inherit_env: bool = True,
        default_timeout_s: float = 300.0,
        facet_observers: Optional[Mapping[str, FacetObserver]] = None,
        deterministic: bool = False,
        clock: Clock = perf_clock,
    ) -> None:
        self.name = name
        self.argv = list(argv)
        self.outputs = parse_output_specs(outputs or {})
        self._env = dict(env or {})
        self._inherit_env = inherit_env
        self._default_timeout_s = default_timeout_s
        self._facet_observers = dict(facet_observers or {})
        self._deterministic = deterministic
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        """Conservative by default.

        An arbitrary external command is assumed non-deterministic and to have
        side effects until a determinism probe says otherwise. Assuming the
        opposite would let a component be shadow-executed on the strength of a
        default value nobody chose.
        """
        return BehaviorContract(
            deterministic=self._deterministic,
            idempotent=self._deterministic,
            retry_safe=True,
            side_effects=["writes files in the invocation workdir"],
            shadow_safe=True,
        )

    def plan(self, inv: Invocation) -> tuple[list[str], Path, dict[str, str]]:
        """Resolve the exact argv, workdir, and environment for ``inv``.

        Separated from :meth:`invoke` so the command can be inspected and
        asserted on without running anything.
        """
        workdir = ensure_workdir(inv, required_by=f"subprocess component '{self.name}'")
        input_paths = materialize_inputs(inv, workdir)
        out_paths = output_paths_for(self.outputs, workdir)
        argv = render_argv(
            self.argv,
            inv,
            workdir=str(workdir),
            input_paths=input_paths,
            output_paths=out_paths,
        )
        return argv, workdir, build_env(inv, overrides=self._env, inherit=self._inherit_env)

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        try:
            argv, workdir, env = self.plan(inv)
        except TemplateError as exc:
            return failure_result(FaultClass.CONFIGURATION, str(exc), exit_code=None)
        except FileNotFoundError as exc:
            return failure_result(FaultClass.ENVIRONMENT, str(exc), exit_code=None)

        for spec in self.outputs.values():
            if spec.source == "file" and spec.path:
                (workdir / spec.path).parent.mkdir(parents=True, exist_ok=True)

        outcome = await run_process(
            argv,
            cwd=workdir,
            env=env,
            timeout_s=inv.timeout_s(self._default_timeout_s),
        )
        cost = CostProfile(latency_s=self._clock() - started)
        return finish_process_invocation(
            inv,
            argv=argv,
            workdir=workdir,
            outcome=outcome,
            specs=self.outputs,
            observers=self._facet_observers,
            cost=cost,
        )


#: First log line of any process-backed invocation: the exact argv that ran,
#: as JSON, so a run can be replayed by hand from the trace alone.
ARGV_LOG_PREFIX = "argv="


def finish_process_invocation(
    inv: Invocation,
    *,
    argv: Sequence[str],
    workdir: Path,
    outcome: ProcessOutcome,
    specs: Mapping[str, OutputSpec],
    observers: Optional[Mapping[str, FacetObserver]],
    cost: CostProfile,
) -> InvocationResult:
    """Turn a finished process into an :class:`InvocationResult`.

    Shared by the subprocess, container, and coding-agent adapters so that all
    three classify failures identically. Divergent classification here would
    show up much later as a diagnoser that behaves differently depending on
    which adapter happened to run a step.
    """
    log_lines = [ARGV_LOG_PREFIX + json.dumps(list(argv))]
    if outcome.stdout:
        log_lines.append("--- stdout ---\n" + outcome.stdout)
    if outcome.stderr:
        log_lines.append("--- stderr ---\n" + outcome.stderr)

    if outcome.launch_error is not None:
        return InvocationResult(
            ok=False,
            cost=cost,
            logs="\n".join(log_lines),
            errors=[
                error_line(
                    FaultClass.ENVIRONMENT,
                    f"could not launch '{argv[0] if argv else ''}': {outcome.launch_error}",
                )
            ],
            exit_code=None,
        )

    if outcome.timed_out:
        return InvocationResult(
            ok=False,
            cost=cost,
            logs="\n".join(log_lines),
            errors=[
                error_line(
                    FaultClass.TOOL_FAILURE,
                    f"process exceeded its wall-time budget and was killed: {list(argv)}",
                )
            ],
            exit_code=outcome.exit_code,
        )

    artifacts, declared, collect_errors = collect_outputs(
        specs, workdir=workdir, outcome=outcome
    )
    errors = list(collect_errors)
    if outcome.exit_code not in (0, None):
        errors.insert(
            0,
            error_line(
                FaultClass.TOOL_FAILURE,
                f"process exited with code {outcome.exit_code}",
            ),
        )

    finalized = finalize_outputs(
        artifacts,
        observers=observers,
        declared_facets=declared,
        producer=inv.producer_id,
        derived_from=input_ids(inv),
    )
    log_lines.extend(finalized.notes)

    return InvocationResult(
        ok=outcome.exit_code == 0 and not errors,
        outputs=finalized.outputs,
        cost=cost,
        logs="\n".join(log_lines),
        errors=errors,
        exit_code=outcome.exit_code,
        observed_facets=finalized.observed_facets,
    )


__all__ = [
    "OutputSpec",
    "ProcessOutcome",
    "run_process",
    "build_env",
    "materialize_inputs",
    "output_paths_for",
    "collect_outputs",
    "parse_output_specs",
    "SubprocessAdapter",
    "ARGV_LOG_PREFIX",
    "finish_process_invocation",
]
