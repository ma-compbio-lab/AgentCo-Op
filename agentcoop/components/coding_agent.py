"""Headless coding agents as *executors* inside a compiled workflow.

Codex and Claude Code are not rivals to this system; they are components. A
compiled workflow can bind one to a subgoal exactly like any other component,
and the comparison harness needs all four arms — agent alone, agent handed the
compiled blueprint and typed contracts, AgentCo-Op driving the agent as its
executor backend, AgentCo-Op with native adapters — to separate "the artifacts
carry the value" from "the model carries the value".

Three properties this adapter must have, and does:

**Importable and testable with neither CLI installed.**
    :meth:`CodingAgentAdapter.plan` is pure: it renders the exact argv, cwd,
    environment, and prompt without touching the filesystem beyond the workdir
    it was given, so the command being sent to a coding agent is a unit-test
    assertion rather than a thing you discover from a shell history.

**The exact argv is recorded.**
    A coding agent's behaviour depends entirely on how it was invoked — model,
    sandbox mode, approval policy, working directory. Every result logs the
    argv as JSON on its first line, so a run can be reproduced or challenged.

**Non-determinism is declared, not discovered.**
    :meth:`behavior_contract` reports ``deterministic=False``,
    ``idempotent=False``, and ``shadow_safe=False``. The last is the
    load-bearing one: a coding agent edits files and runs commands, so the
    repair layer must refuse to speculatively execute it during shadow
    validation and escalate instead. A default-permissive contract here would
    let shadow validation mutate a real repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Optional, Sequence

from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.faults import FaultClass

from agentcoop.components.base import (
    Clock,
    FacetObserver,
    Invocation,
    InvocationResult,
    TemplateError,
    ensure_workdir,
    error_line,
    failure_result,
    perf_clock,
    render_argv,
    render_template,
    standard_resolver,
)
from agentcoop.components.subprocess_adapter import (
    ARGV_LOG_PREFIX,
    build_env,
    finish_process_invocation,
    materialize_inputs,
    output_paths_for,
    parse_output_specs,
    run_process,
)


class ExecutorPreset(NamedTuple):
    """How to invoke one headless coding-agent CLI.

    Kept as data rather than as branches in :meth:`plan` because these command
    lines change with CLI releases. ``{prompt}`` is substituted with the
    rendered prompt; every other token is passed through the standard argv
    template resolver, so ``{workdir}`` / ``{out:T}`` work here too.
    """

    binary: str
    argv: tuple[str, ...]
    #: Set when the CLI takes the prompt on stdin instead of in argv.
    prompt_on_stdin: bool = False


#: Built-in presets. Override with ``argv=`` when a CLI version differs; the
#: adapter records whatever it actually ran, so a stale preset is visible in
#: the trace rather than silently wrong.
EXECUTOR_PRESETS: dict[str, ExecutorPreset] = {
    "codex": ExecutorPreset(
        binary="codex",
        argv=("codex", "exec", "--cd", "{workdir}", "{prompt}"),
    ),
    "claude": ExecutorPreset(
        binary="claude",
        argv=("claude", "-p", "{prompt}", "--output-format", "text"),
    ),
}

#: Placeholder replaced by the rendered prompt. Not part of the shared
#: template resolver because the prompt is itself a rendered template and
#: nesting the two would make quoting ambiguous.
PROMPT_TOKEN = "{prompt}"


class CodingAgentPlan(NamedTuple):
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    prompt: str
    stdin: Optional[str]


class CodingAgentAdapter:
    """Run a headless coding agent as a workflow node."""

    def __init__(
        self,
        name: str,
        *,
        executor: str = "codex",
        binary: Optional[str] = None,
        argv: Optional[Sequence[str]] = None,
        prompt_template: str = "{cfg:instruction}",
        outputs: Optional[Mapping[str, Any]] = None,
        env: Optional[Mapping[str, str]] = None,
        default_timeout_s: float = 1800.0,
        facet_observers: Optional[Mapping[str, FacetObserver]] = None,
        clock: Clock = perf_clock,
    ) -> None:
        preset = EXECUTOR_PRESETS.get(executor)
        if preset is None and argv is None:
            raise ValueError(
                f"unknown coding-agent executor '{executor}'; "
                f"known: {', '.join(sorted(EXECUTOR_PRESETS))}, or pass argv= explicitly"
            )
        self.name = name
        self.executor = executor
        self.argv_template = list(argv) if argv is not None else list(preset.argv)  # type: ignore[union-attr]
        self.binary = binary or (preset.binary if preset else self.argv_template[0])
        self.prompt_on_stdin = bool(preset.prompt_on_stdin) if preset and argv is None else False
        self.prompt_template = prompt_template
        self.outputs = parse_output_specs(outputs or {})
        self._env = dict(env or {})
        self._default_timeout_s = default_timeout_s
        self._facet_observers = dict(facet_observers or {})
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        """A general-purpose agent editing a repository is not shadow-safe.

        ``shadow_safe=False`` is what stops :class:`ShadowValidator` from
        speculatively re-running this node on reduced input during repair.
        ``deterministic=False`` stops two invocations from being counted as
        independent evidence for the same conclusion.
        """
        return BehaviorContract(
            deterministic=False,
            idempotent=False,
            retry_safe=True,
            side_effects=[
                "edits files in the working directory",
                "executes arbitrary shell commands",
                "calls a paid third-party model API",
            ],
            shadow_safe=False,
        )

    # -- planning -----------------------------------------------------------

    def render_prompt(self, inv: Invocation) -> str:
        template = str(inv.config.get("prompt_template", self.prompt_template))
        return render_template(template, standard_resolver(inv, workdir=str(inv.workdir or "")))

    def plan(self, inv: Invocation) -> CodingAgentPlan:
        """Exactly what would be executed. Pure with respect to the CLI.

        Callable — and asserted on — with neither ``codex`` nor ``claude``
        installed, which is what makes the executor path testable in CI.
        """
        workdir = ensure_workdir(inv, required_by=f"coding agent component '{self.name}'")
        prompt = self.render_prompt(inv)
        input_paths = materialize_inputs(inv, workdir)
        out_paths = output_paths_for(self.outputs, workdir)

        template = [self.binary if i == 0 else tok for i, tok in enumerate(self.argv_template)]
        if self.prompt_on_stdin:
            template = [tok for tok in template if tok != PROMPT_TOKEN]

        rendered: list[str] = []
        for token in template:
            if token == PROMPT_TOKEN:
                rendered.append(prompt)
                continue
            rendered.extend(
                render_argv(
                    [token],
                    inv,
                    workdir=str(workdir),
                    input_paths=input_paths,
                    output_paths=out_paths,
                )
            )

        return CodingAgentPlan(
            argv=rendered,
            cwd=workdir,
            env=build_env(inv, overrides=self._env, inherit=True),
            prompt=prompt,
            stdin=prompt if self.prompt_on_stdin else None,
        )

    def available(self) -> bool:
        """Whether the CLI is on PATH. Never consulted to decide a fallback."""
        import shutil

        return shutil.which(self.binary) is not None

    # -- invocation ---------------------------------------------------------

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        try:
            plan = self.plan(inv)
        except TemplateError as exc:
            return failure_result(FaultClass.CONFIGURATION, str(exc))
        except FileNotFoundError as exc:
            return failure_result(FaultClass.ENVIRONMENT, str(exc))

        planned_log = ARGV_LOG_PREFIX + json.dumps(plan.argv)

        if not self.available():
            # Same principle as ContainerAdapter: no substitute execution. A
            # coding-agent node that quietly degraded to "do nothing, exit 0"
            # would make the executor-backend arm of the comparison meaningless.
            return InvocationResult(
                ok=False,
                cost=CostProfile(latency_s=self._clock() - started),
                logs=planned_log,
                errors=[
                    error_line(
                        FaultClass.ENVIRONMENT,
                        f"coding-agent CLI '{self.binary}' is not on PATH; "
                        f"component '{self.name}' was not executed",
                    )
                ],
                exit_code=None,
            )

        for spec in self.outputs.values():
            if spec.source == "file" and spec.path:
                (plan.cwd / spec.path).parent.mkdir(parents=True, exist_ok=True)

        outcome = await run_process(
            plan.argv,
            cwd=plan.cwd,
            env=plan.env,
            timeout_s=inv.timeout_s(self._default_timeout_s),
            stdin=plan.stdin,
        )
        result = finish_process_invocation(
            inv,
            argv=plan.argv,
            workdir=plan.cwd,
            outcome=outcome,
            specs=self.outputs,
            observers=self._facet_observers,
            cost=CostProfile(latency_s=self._clock() - started),
        )
        # A coding agent's output is only interpretable alongside the prompt it
        # was given, so the prompt travels with the result.
        return result.model_copy(
            update={"logs": f"prompt=\n{plan.prompt}\n{result.logs}"}
        )


__all__ = [
    "ExecutorPreset",
    "EXECUTOR_PRESETS",
    "PROMPT_TOKEN",
    "CodingAgentPlan",
    "CodingAgentAdapter",
]
