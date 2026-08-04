"""Run a component inside a container.

The single most important line in this module is the one that *refuses* to do
anything when Docker is missing.

Containerization is how AgentCo-Op backs its isolation claims: pinned
environments make `EnvironmentContract.conflicts_with` meaningful, `--network
none` makes the determinism probe honest, and a bind-mounted workdir makes the
artifact set the only channel between components. A silent fallback to host
execution would preserve none of those properties while continuing to report
success, which means every isolation claim in the paper would rest on whether
the machine that produced the numbers happened to have Docker installed. So a
missing Docker binary is an ``ENVIRONMENT``-classed failure with ``ok=False``,
and the planned command is written to the logs so the operator can see exactly
what did not run.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

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
)
from agentcoop.components.subprocess_adapter import (
    ARGV_LOG_PREFIX,
    build_env,
    finish_process_invocation,
    output_paths_for,
    parse_output_specs,
    run_process,
)


def docker_available(binary: str = "docker") -> bool:
    """Whether the container runtime is on PATH.

    Deliberately a plain PATH lookup: no ``docker info`` call, because that
    would put a network/daemon round-trip on the invocation path and make the
    check itself a source of flakiness. A present-but-broken daemon still
    surfaces, as a launch failure from :func:`run_process`.
    """
    return shutil.which(binary) is not None


class ContainerAdapter:
    """``docker run`` a component with the workdir bind-mounted."""

    def __init__(
        self,
        name: str,
        image: str,
        argv: Sequence[str] = (),
        *,
        outputs: Optional[Mapping[str, Any]] = None,
        docker_binary: str = "docker",
        mount_point: str = "/work",
        network: bool = False,
        memory_gb: Optional[float] = None,
        env: Optional[Mapping[str, str]] = None,
        extra_run_args: Sequence[str] = (),
        run_as_current_user: bool = True,
        default_timeout_s: float = 900.0,
        facet_observers: Optional[Mapping[str, FacetObserver]] = None,
        deterministic: bool = False,
        clock: Clock = perf_clock,
    ) -> None:
        self.name = name
        self.image = image
        self.argv = list(argv)
        self.outputs = parse_output_specs(outputs or {})
        self.docker_binary = docker_binary
        self.mount_point = mount_point.rstrip("/") or "/work"
        self.network = network
        self.memory_gb = memory_gb
        self._env = dict(env or {})
        self._extra_run_args = list(extra_run_args)
        self._run_as_current_user = run_as_current_user
        self._default_timeout_s = default_timeout_s
        self._facet_observers = dict(facet_observers or {})
        self._deterministic = deterministic
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        return BehaviorContract(
            deterministic=self._deterministic,
            idempotent=self._deterministic,
            retry_safe=True,
            side_effects=[f"writes files into the bind-mounted workdir at {self.mount_point}"]
            + (["container has network access"] if self.network else []),
            shadow_safe=True,
        )

    # -- planning -----------------------------------------------------------

    def container_paths(self, inv: Invocation, workdir: Path) -> dict[str, str]:
        """Materialize inputs under the mount and return container-side paths.

        Everything the component reads must live under the single bind mount.
        An input that lives elsewhere on the host is copied in rather than
        mounted separately, so the isolation boundary stays one directory wide
        and is therefore auditable.
        """
        paths: dict[str, str] = {}
        input_dir = workdir / "inputs"
        for type_name in sorted(inv.inputs):
            art = inv.inputs[type_name]
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in type_name)
            if art.path:
                source = Path(art.path)
                try:
                    relative = source.resolve().relative_to(workdir.resolve())
                    paths[type_name] = f"{self.mount_point}/{relative.as_posix()}"
                    continue
                except (ValueError, OSError):
                    input_dir.mkdir(parents=True, exist_ok=True)
                    target = input_dir / f"{safe}{source.suffix}"
                    shutil.copyfile(source, target)
                    paths[type_name] = f"{self.mount_point}/inputs/{target.name}"
                    continue
            input_dir.mkdir(parents=True, exist_ok=True)
            target = input_dir / f"{safe}.json"
            target.write_text(
                json.dumps(art.payload, sort_keys=True, indent=2, default=str),
                encoding="utf-8",
            )
            paths[type_name] = f"{self.mount_point}/inputs/{target.name}"
        return paths

    def docker_argv(self, inv: Invocation, workdir: Path) -> list[str]:
        """The exact ``docker run`` command line, fully rendered.

        Deterministic in argument order (env vars sorted) so two runs of the
        same invocation produce a byte-identical command that can be diffed.
        """
        input_paths = self.container_paths(inv, workdir)
        out_paths = output_paths_for(self.outputs, workdir, root=self.mount_point)
        inner = render_argv(
            self.argv,
            inv,
            workdir=self.mount_point,
            input_paths=input_paths,
            output_paths=out_paths,
        )

        run_args: list[str] = [
            self.docker_binary,
            "run",
            "--rm",
            "-v",
            f"{workdir}:{self.mount_point}",
            "-w",
            self.mount_point,
        ]
        # No network unless the environment contract asked for it: an
        # unpinned network is the most common reason a "deterministic"
        # component is not.
        run_args += ["--network", "bridge" if self.network else "none"]
        if self.memory_gb:
            run_args += ["--memory", f"{self.memory_gb}g"]
        if self._run_as_current_user and hasattr(os, "getuid"):
            run_args += ["--user", f"{os.getuid()}:{os.getgid()}"]

        container_env = {
            "PYTHONHASHSEED": str(inv.seed),
            "AGENTCOOP_SEED": str(inv.seed),
            **{str(k): str(v) for k, v in self._env.items()},
        }
        for key in sorted(container_env):
            run_args += ["-e", f"{key}={container_env[key]}"]

        run_args += list(self._extra_run_args)
        run_args.append(self.image)
        run_args.extend(inner)
        return run_args

    # -- invocation ---------------------------------------------------------

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()

        if not docker_available(self.docker_binary):
            return self._no_docker(inv, started)

        try:
            workdir = ensure_workdir(inv, required_by=f"container component '{self.name}'")
            argv = self.docker_argv(inv, workdir)
        except TemplateError as exc:
            return failure_result(FaultClass.CONFIGURATION, str(exc))
        except (FileNotFoundError, OSError) as exc:
            return failure_result(FaultClass.ENVIRONMENT, str(exc))

        for spec in self.outputs.values():
            if spec.source == "file" and spec.path:
                (workdir / spec.path).parent.mkdir(parents=True, exist_ok=True)

        outcome = await run_process(
            argv,
            cwd=workdir,
            env=build_env(inv, overrides={}, inherit=True),
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

    def _no_docker(self, inv: Invocation, started: float) -> InvocationResult:
        """Refuse, loudly, and show what would have run.

        The planned command is best-effort: if the workdir is also missing we
        still report the Docker problem, because that is the fault the operator
        must fix first.
        """
        planned = ""
        try:
            workdir = ensure_workdir(inv, required_by=f"container component '{self.name}'")
            planned = ARGV_LOG_PREFIX + json.dumps(self.docker_argv(inv, workdir))
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real fault
            planned = ARGV_LOG_PREFIX + json.dumps(
                [self.docker_binary, "run", "--rm", "...", self.image, *self.argv]
            )
        return InvocationResult(
            ok=False,
            cost=CostProfile(latency_s=self._clock() - started),
            logs=planned,
            errors=[
                error_line(
                    FaultClass.ENVIRONMENT,
                    f"container runtime '{self.docker_binary}' is not on PATH; "
                    f"component '{self.name}' declares image '{self.image}' and will not "
                    "be executed on the host — a host fallback would silently void the "
                    "isolation, dependency-pinning, and network-off guarantees this "
                    "component was certified under",
                )
            ],
            exit_code=None,
        )


__all__ = ["docker_available", "ContainerAdapter"]
