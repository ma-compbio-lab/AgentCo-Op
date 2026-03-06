from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlparse, urlunparse

from dynaforge.ir.schema import MCPServerRef, SandboxSpec, Transport


class SandboxError(RuntimeError):
    """Raised when a sandbox cannot be built, started, or executed."""


@dataclass
class MaterializedSandbox:
    sandbox_id: str
    image: str
    container_name: str
    container_id: str
    mcp_url: Optional[str] = None


class DockerSandboxRunner:
    """Materializes sandboxes as real Docker containers and optionally builds them via Repo2Run."""

    def __init__(
        self,
        *,
        docker_cmd: str = "docker",
        repo2run_cmd: str = "repo2run",
    ):
        self.docker_cmd = docker_cmd
        self.repo2run_cmd = repo2run_cmd
        self._materialized: Dict[str, MaterializedSandbox] = {}

    def is_materialized(self, sandbox_id: str) -> bool:
        return sandbox_id in self._materialized

    def ensure_materialized(self, sandbox: SandboxSpec) -> MaterializedSandbox:
        existing = self._materialized.get(sandbox.sandbox_id)
        if existing is not None:
            return existing

        image = self._ensure_image(sandbox)
        container_name = sandbox.container_name or f"dynaforge-{sandbox.sandbox_id}"
        run_cmd = self._build_run_command(sandbox, image=image, container_name=container_name)
        completed = self._run(run_cmd, check=True)
        container_id = (completed.stdout or "").strip() or container_name
        materialized = MaterializedSandbox(
            sandbox_id=sandbox.sandbox_id,
            image=image,
            container_name=container_name,
            container_id=container_id,
            mcp_url=self._resolved_mcp_url(sandbox),
        )
        self._materialized[sandbox.sandbox_id] = materialized
        return materialized

    def materialize_server_ref(self, server_ref: MCPServerRef) -> MCPServerRef:
        if server_ref.sandbox is None:
            return server_ref

        materialized = self.ensure_materialized(server_ref.sandbox)
        if server_ref.transport == Transport.stdio:
            stdio_cmd = server_ref.stdio_cmd or server_ref.sandbox.cmd
            if not stdio_cmd:
                raise SandboxError(
                    f"Sandbox-backed stdio server '{server_ref.name}' requires stdio_cmd or sandbox.cmd"
                )
            wrapped_cmd = [self.docker_cmd, "exec", "-i", materialized.container_name, *stdio_cmd]
            return server_ref.model_copy(update={"stdio_cmd": wrapped_cmd})

        resolved_url = server_ref.url or materialized.mcp_url
        if not resolved_url:
            raise SandboxError(
                f"Sandbox-backed server '{server_ref.name}' requires a URL or inferable MCP port"
            )
        return server_ref.model_copy(update={"url": resolved_url})

    def run_in_sandbox(
        self,
        sandbox: SandboxSpec,
        cmd: Sequence[str],
        *,
        timeout_s: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        materialized = self.ensure_materialized(sandbox)
        exec_cmd: List[str] = [self.docker_cmd, "exec"]
        merged_env = dict(sandbox.env)
        if env:
            merged_env.update(env)
        for key, value in sorted(merged_env.items()):
            exec_cmd.extend(["-e", f"{key}={value}"])
        exec_cmd.append(materialized.container_name)
        exec_cmd.extend(cmd)
        return self._run(exec_cmd, check=False, timeout=timeout_s)

    def cleanup_all(self) -> None:
        for materialized in list(self._materialized.values()):
            self._run(
                [self.docker_cmd, "rm", "-f", materialized.container_name],
                check=False,
            )
        self._materialized.clear()

    def _ensure_image(self, sandbox: SandboxSpec) -> str:
        image = sandbox.repo2run.image_tag if sandbox.repo2run and sandbox.repo2run.image_tag else sandbox.image
        if sandbox.repo2run is None:
            return image

        build_cmd = list(sandbox.repo2run.build_cmd)
        if not build_cmd:
            build_cmd = [self.repo2run_cmd, sandbox.repo2run.source]
        self._run(build_cmd, check=True, env=sandbox.repo2run.env)

        if sandbox.repo2run.smoke_test_cmd:
            smoke_cmd = [self.docker_cmd, "run", "--rm", image, *sandbox.repo2run.smoke_test_cmd]
            self._run(smoke_cmd, check=True)
        return image

    def _build_run_command(self, sandbox: SandboxSpec, *, image: str, container_name: str) -> List[str]:
        command: List[str] = [self.docker_cmd, "run", "-d"]
        if sandbox.auto_remove:
            command.append("--rm")
        command.extend(["--name", container_name])

        if not sandbox.safety.allow_network:
            command.extend(["--network", "none"])
        if sandbox.safety.cpu_quota is not None:
            command.extend(["--cpus", str(sandbox.safety.cpu_quota)])
        if sandbox.safety.mem_limit_mb is not None:
            command.extend(["--memory", f"{sandbox.safety.mem_limit_mb}m"])
        if sandbox.workdir:
            command.extend(["-w", sandbox.workdir])
        for key, value in sorted(sandbox.env.items()):
            command.extend(["-e", f"{key}={value}"])
        for mount in sandbox.mounts:
            mount_spec = f"{mount.host_path}:{mount.container_path}"
            if mount.read_only:
                mount_spec = f"{mount_spec}:ro"
            command.extend(["-v", mount_spec])

        if sandbox.mcp_transport in {Transport.streamable_http, Transport.sse}:
            container_port = sandbox.exposed_port or self._infer_port(sandbox)
            host_port = sandbox.host_port or container_port
            command.extend(["-p", f"{host_port}:{container_port}"])

        extra_args: List[str] = []
        if sandbox.entrypoint:
            command.extend(["--entrypoint", sandbox.entrypoint[0]])
            extra_args.extend(sandbox.entrypoint[1:])

        command.append(image)
        command.extend(extra_args)
        if sandbox.cmd:
            command.extend(sandbox.cmd)
        return command

    def _resolved_mcp_url(self, sandbox: SandboxSpec) -> Optional[str]:
        if sandbox.mcp_transport == Transport.stdio:
            return None
        port = sandbox.host_port or sandbox.exposed_port or self._infer_port(sandbox)
        if sandbox.mcp_url:
            parsed = urlparse(sandbox.mcp_url)
            scheme = parsed.scheme or "http"
            path = parsed.path or "/mcp"
            netloc = f"127.0.0.1:{port}"
            return urlunparse((scheme, netloc, path, "", "", ""))
        return f"http://127.0.0.1:{port}/mcp"

    @staticmethod
    def _infer_port(sandbox: SandboxSpec) -> int:
        if sandbox.mcp_url:
            parsed = urlparse(sandbox.mcp_url)
            if parsed.port:
                return parsed.port
        return 8000

    def _run(
        self,
        cmd: Sequence[str],
        *,
        check: bool,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                check=check,
                timeout=timeout,
                env={**os.environ, **env} if env else None,
            )
        except FileNotFoundError as exc:
            raise SandboxError(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            message = exc.stderr or exc.stdout or str(exc)
            raise SandboxError(message) from exc
        except subprocess.SubprocessError as exc:
            raise SandboxError(str(exc)) from exc
