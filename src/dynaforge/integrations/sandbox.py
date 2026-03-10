from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence
from urllib.parse import urlparse, urlunparse

from dynaforge.ir.schema import MCPServerRef, SandboxBackend, SandboxSpec, Transport


class SandboxError(RuntimeError):
    """Raised when a sandbox cannot be built, started, or executed."""


@dataclass
class MaterializedSandbox:
    sandbox_id: str
    backend: SandboxBackend
    image: str
    container_name: Optional[str] = None
    container_id: Optional[str] = None
    mcp_url: Optional[str] = None
    root_dir: Optional[str] = None
    python_bin: Optional[str] = None
    repo_dir: Optional[str] = None


class SandboxRunner(Protocol):
    def is_materialized(self, sandbox_id: str) -> bool:
        ...

    def ensure_materialized(self, sandbox: SandboxSpec) -> MaterializedSandbox:
        ...

    def materialize_server_ref(self, server_ref: MCPServerRef) -> MCPServerRef:
        ...

    def run_in_sandbox(
        self,
        sandbox: SandboxSpec,
        cmd: Sequence[str],
        *,
        timeout_s: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        ...

    def cleanup_all(self) -> None:
        ...


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
            backend=SandboxBackend.docker,
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
            wrapped_cmd = [self.docker_cmd, "exec", "-i", materialized.container_name or "", *stdio_cmd]
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
        exec_cmd.append(materialized.container_name or "")
        exec_cmd.extend(cmd)
        return self._run(exec_cmd, check=False, timeout=timeout_s)

    def cleanup_all(self) -> None:
        for materialized in list(self._materialized.values()):
            if materialized.container_name:
                self._run([self.docker_cmd, "rm", "-f", materialized.container_name], check=False)
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


class LocalVenvSandboxRunner:
    """Materializes sandboxes as isolated per-sandbox virtual environments under the project directory."""

    def __init__(
        self,
        *,
        sandbox_root: str | Path = ".dynaforge_sandboxes",
        git_cmd: str = "git",
        python_cmd: str = sys.executable,
        project_root: str | Path | None = None,
    ):
        self.sandbox_root = Path(sandbox_root).resolve()
        self.git_cmd = git_cmd
        self.python_cmd = python_cmd
        self.project_root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self._materialized: Dict[str, MaterializedSandbox] = {}

    def is_materialized(self, sandbox_id: str) -> bool:
        return sandbox_id in self._materialized

    def ensure_materialized(self, sandbox: SandboxSpec) -> MaterializedSandbox:
        existing = self._materialized.get(sandbox.sandbox_id)
        if existing is not None:
            return existing

        root_dir = self.sandbox_root / sandbox.sandbox_id
        root_dir.mkdir(parents=True, exist_ok=True)
        venv_dir = root_dir / "venv"
        if not venv_dir.exists():
            try:
                venv.EnvBuilder(with_pip=True).create(str(venv_dir))
            except Exception as exc:
                raise SandboxError(f"Failed to create virtualenv for sandbox {sandbox.sandbox_id}: {exc}") from exc

        python_bin = self._python_bin(venv_dir)
        repo_dir: Optional[Path] = None

        self._run_local(
            [str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            cwd=root_dir,
            env=sandbox.env,
            check=True,
        )

        if sandbox.repo2run is not None:
            repo_dir = self._materialize_repo_source(root_dir, sandbox)
            self._install_repo(root_dir, repo_dir, sandbox, python_bin)

        for bootstrap_cmd in sandbox.bootstrap_cmds:
            self._run_local(
                self._rewrite_python_cmd(bootstrap_cmd, python_bin),
                cwd=self._resolve_workdir(sandbox, root_dir, repo_dir),
                env=self._sandbox_env(sandbox, root_dir, python_bin, repo_dir),
                check=True,
            )

        if sandbox.repo2run and sandbox.repo2run.smoke_test_cmd:
            self.run_in_sandbox(sandbox, sandbox.repo2run.smoke_test_cmd, env=sandbox.repo2run.env)

        materialized = MaterializedSandbox(
            sandbox_id=sandbox.sandbox_id,
            backend=SandboxBackend.local_venv,
            image=sandbox.image,
            root_dir=str(root_dir),
            python_bin=str(python_bin),
            repo_dir=str(repo_dir) if repo_dir is not None else None,
            mcp_url=sandbox.mcp_url,
        )
        self._materialized[sandbox.sandbox_id] = materialized
        return materialized

    def materialize_server_ref(self, server_ref: MCPServerRef) -> MCPServerRef:
        if server_ref.sandbox is None:
            return server_ref
        materialized = self.ensure_materialized(server_ref.sandbox)
        if server_ref.transport != Transport.stdio:
            resolved_url = server_ref.url or materialized.mcp_url
            if not resolved_url:
                raise SandboxError(
                    f"Local venv sandbox '{server_ref.name}' currently supports stdio MCP servers only"
                )
            return server_ref.model_copy(update={"url": resolved_url})

        stdio_cmd = list(server_ref.stdio_cmd or server_ref.sandbox.cmd or [])
        if not stdio_cmd:
            raise SandboxError(f"Sandbox-backed stdio server '{server_ref.name}' requires stdio_cmd or sandbox.cmd")
        wrapped_cmd = self._rewrite_python_cmd(stdio_cmd, Path(materialized.python_bin or sys.executable))
        server_env = dict(server_ref.env)
        server_env.update(self._sandbox_env(server_ref.sandbox, Path(materialized.root_dir or "."), Path(materialized.python_bin or sys.executable), Path(materialized.repo_dir) if materialized.repo_dir else None))
        return server_ref.model_copy(update={"stdio_cmd": wrapped_cmd, "env": server_env})

    def run_in_sandbox(
        self,
        sandbox: SandboxSpec,
        cmd: Sequence[str],
        *,
        timeout_s: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        materialized = self.ensure_materialized(sandbox)
        root_dir = Path(materialized.root_dir or self.sandbox_root / sandbox.sandbox_id)
        repo_dir = Path(materialized.repo_dir) if materialized.repo_dir else None
        python_bin = Path(materialized.python_bin or sys.executable)
        merged_env = self._sandbox_env(sandbox, root_dir, python_bin, repo_dir)
        if env:
            merged_env.update(env)
        return self._run_local(
            self._rewrite_python_cmd(cmd, python_bin),
            cwd=self._resolve_workdir(sandbox, root_dir, repo_dir),
            env=merged_env,
            check=False,
            timeout=timeout_s,
        )

    def cleanup_all(self) -> None:
        self._materialized.clear()

    def _materialize_repo_source(self, root_dir: Path, sandbox: SandboxSpec) -> Path:
        assert sandbox.repo2run is not None
        source = sandbox.repo2run.source
        repo_dir = root_dir / "repo"
        if repo_dir.exists():
            if (repo_dir / ".git").exists() or (repo_dir / "pyproject.toml").exists() or (repo_dir / "setup.py").exists():
                return repo_dir
            shutil.rmtree(repo_dir)
        if self._looks_like_git_source(source):
            self._run_local([self.git_cmd, "clone", "--depth", "1", source, str(repo_dir)], cwd=root_dir, check=True)
            return repo_dir
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            raise SandboxError(f"Sandbox repo source does not exist: {source}")
        if (source_path / ".git").exists():
            self._run_local(
                [self.git_cmd, "clone", "--depth", "1", str(source_path), str(repo_dir)],
                cwd=root_dir,
                check=True,
            )
            return repo_dir
        shutil.copytree(source_path, repo_dir)
        return repo_dir

    def _install_repo(self, root_dir: Path, repo_dir: Path, sandbox: SandboxSpec, python_bin: Path) -> None:
        assert sandbox.repo2run is not None
        build_cmd = list(sandbox.repo2run.build_cmd)
        env = self._sandbox_env(sandbox, root_dir, python_bin, repo_dir)
        env.update(sandbox.repo2run.env)
        if build_cmd and build_cmd[0] != "repo2run":
            self._run_local(
                self._rewrite_python_cmd(build_cmd, python_bin),
                cwd=repo_dir,
                env=env,
                check=True,
            )
            return
        self._run_local(
            [str(python_bin), "-m", "pip", "install", "-e", str(repo_dir)],
            cwd=repo_dir,
            env=env,
            check=True,
        )

    def _sandbox_env(
        self,
        sandbox: SandboxSpec,
        root_dir: Path,
        python_bin: Path,
        repo_dir: Optional[Path],
    ) -> Dict[str, str]:
        env = dict(os.environ)
        env.update(sandbox.env)
        bin_dir = str(python_bin.parent)
        env["VIRTUAL_ENV"] = str(python_bin.parent.parent)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["DYNaforge_SANDBOX_ROOT"] = str(root_dir)
        if repo_dir is not None:
            env["DYNaforge_SANDBOX_REPO"] = str(repo_dir)
        existing_pythonpath = env.get("PYTHONPATH", "")
        pythonpath_entries = [str(self.project_root)]
        src_dir = self.project_root / "src"
        if src_dir.exists():
            pythonpath_entries.insert(0, str(src_dir))
        if existing_pythonpath:
            pythonpath_entries.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
        return env

    @staticmethod
    def _resolve_workdir(sandbox: SandboxSpec, root_dir: Path, repo_dir: Optional[Path]) -> Path:
        if sandbox.workdir:
            workdir = Path(sandbox.workdir)
            if workdir.is_absolute():
                return workdir
            return root_dir / workdir
        if repo_dir is not None:
            return repo_dir
        return root_dir

    @staticmethod
    def _python_bin(venv_dir: Path) -> Path:
        if os.name == "nt":
            return venv_dir / "Scripts" / "python.exe"
        return venv_dir / "bin" / "python"

    @staticmethod
    def _rewrite_python_cmd(cmd: Sequence[str], python_bin: Path) -> List[str]:
        command = list(cmd)
        if command and command[0] in {"python", "python3", sys.executable}:
            command[0] = str(python_bin)
        return command

    @staticmethod
    def _looks_like_git_source(source: str) -> bool:
        return source.startswith(("http://", "https://", "git@", "ssh://")) or source.endswith(".git")

    def _run_local(
        self,
        cmd: Sequence[str],
        *,
        cwd: Path,
        check: bool,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                check=check,
                timeout=timeout,
                cwd=str(cwd),
                env=env,
            )
        except FileNotFoundError as exc:
            raise SandboxError(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            message = exc.stderr or exc.stdout or str(exc)
            raise SandboxError(message) from exc
        except subprocess.SubprocessError as exc:
            raise SandboxError(str(exc)) from exc


class CompositeSandboxRunner:
    """Dispatches sandbox requests to the configured backend for each sandbox."""

    def __init__(
        self,
        *,
        docker_runner: Optional[DockerSandboxRunner] = None,
        local_runner: Optional[LocalVenvSandboxRunner] = None,
    ):
        self.docker_runner = docker_runner or DockerSandboxRunner()
        self.local_runner = local_runner or LocalVenvSandboxRunner()

    def is_materialized(self, sandbox_id: str) -> bool:
        return self.docker_runner.is_materialized(sandbox_id) or self.local_runner.is_materialized(sandbox_id)

    def ensure_materialized(self, sandbox: SandboxSpec) -> MaterializedSandbox:
        return self._runner_for(sandbox).ensure_materialized(sandbox)

    def materialize_server_ref(self, server_ref: MCPServerRef) -> MCPServerRef:
        if server_ref.sandbox is None:
            return server_ref
        return self._runner_for(server_ref.sandbox).materialize_server_ref(server_ref)

    def run_in_sandbox(
        self,
        sandbox: SandboxSpec,
        cmd: Sequence[str],
        *,
        timeout_s: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._runner_for(sandbox).run_in_sandbox(sandbox, cmd, timeout_s=timeout_s, env=env)

    def cleanup_all(self) -> None:
        self.docker_runner.cleanup_all()
        self.local_runner.cleanup_all()

    def _runner_for(self, sandbox: SandboxSpec):
        if sandbox.backend == SandboxBackend.local_venv:
            return self.local_runner
        return self.docker_runner
