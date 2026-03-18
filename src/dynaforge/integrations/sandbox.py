from __future__ import annotations

import json
import os
import re
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
        python_bin = self._materialize_python_runtime(root_dir, sandbox)
        repo_dir: Optional[Path] = None
        manifest_path = root_dir / "materialization_manifest.json"
        expected_manifest = self._materialization_manifest(sandbox)

        if sandbox.repo2run is not None:
            repo_dir = self._materialize_repo_source(root_dir, sandbox)

        if self._materialization_matches(manifest_path, expected_manifest):
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

        upgrade_result = self._run_local(
            [str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            cwd=root_dir,
            env=sandbox.env,
            check=False,
        )
        if upgrade_result.returncode != 0:
            # This upgrade step is opportunistic. The freshly created venv already has a usable pip.
            # Continuing avoids treating transient index/DNS failures as sandbox-materialization failures.
            pass

        if sandbox.repo2run is not None and repo_dir is not None:
            self._install_repo(root_dir, repo_dir, sandbox, python_bin)

        for bootstrap_cmd in sandbox.bootstrap_cmds:
            rewritten_cmd = self._rewrite_python_cmd(bootstrap_cmd, python_bin)
            try:
                self._run_local(
                    rewritten_cmd,
                    cwd=self._resolve_workdir(sandbox, root_dir, repo_dir),
                    env=self._sandbox_env(sandbox, root_dir, python_bin, repo_dir),
                    check=True,
                )
            except SandboxError:
                if not self._is_pip_like_command(rewritten_cmd):
                    raise
                self._run_local(
                    rewritten_cmd,
                    cwd=self._resolve_workdir(sandbox, root_dir, repo_dir),
                    env=self._sandbox_env(sandbox, root_dir, python_bin, repo_dir),
                    check=True,
                )

        if sandbox.repo2run and sandbox.repo2run.smoke_test_cmd:
            self.run_in_sandbox(sandbox, sandbox.repo2run.smoke_test_cmd, env=sandbox.repo2run.env)

        manifest_path.write_text(json.dumps(expected_manifest, ensure_ascii=True, indent=2), encoding="utf-8")

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

    @staticmethod
    def _materialization_manifest(sandbox: SandboxSpec) -> Dict[str, object]:
        repo_spec: Dict[str, object] = {}
        if sandbox.repo2run is not None:
            repo_spec = {
                "source": sandbox.repo2run.source,
                "build_cmd": list(sandbox.repo2run.build_cmd or []),
                "smoke_test_cmd": list(sandbox.repo2run.smoke_test_cmd or []),
                "env": dict(sandbox.repo2run.env),
            }
        return {
            "image": sandbox.image,
            "backend": sandbox.backend.value,
            "python_runtime": LocalVenvSandboxRunner._python_runtime_identity(sandbox),
            "repo2run": repo_spec,
            "bootstrap_cmds": [list(cmd) for cmd in sandbox.bootstrap_cmds],
        }

    @staticmethod
    def _materialization_matches(path: Path, expected: Dict[str, object]) -> bool:
        if not path.exists():
            return False
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if current == expected:
            return True

        # Backward compatibility for manifests written before explicit
        # python-runtime tracking was introduced.
        if "python_runtime" in expected and "python_runtime" not in current:
            current_runtime = f"{sys.version_info.major}.{sys.version_info.minor}"
            if expected.get("python_runtime") == current_runtime:
                upgraded_current = dict(current)
                upgraded_current["python_runtime"] = current_runtime
                if upgraded_current == expected:
                    return True
        return False

    @staticmethod
    def _is_pip_like_command(cmd: Sequence[str]) -> bool:
        lowered = [str(item).lower() for item in cmd]
        return any(part.endswith("/pip") or part == "pip" or part.startswith("pip") for part in lowered)

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
            rewritten_cmd = self._rewrite_python_cmd(build_cmd, python_bin)
            try:
                self._run_local(
                    rewritten_cmd,
                    cwd=repo_dir,
                    env=env,
                    check=True,
                )
            except SandboxError as exc:
                if not self._should_retry_without_build_isolation(rewritten_cmd, exc):
                    raise
                self._run_local(
                    self._inject_no_build_isolation(rewritten_cmd),
                    cwd=repo_dir,
                    env=env,
                    check=True,
                )
            return
        if (repo_dir / "setup.py").exists() and not (repo_dir / "pyproject.toml").exists():
            fallback_cmd = [str(python_bin), "setup.py", "develop"]
        else:
            fallback_cmd = [str(python_bin), "-m", "pip", "install", "-e", str(repo_dir)]
        try:
            self._run_local(
                fallback_cmd,
                cwd=repo_dir,
                env=env,
                check=True,
            )
        except SandboxError as exc:
            if not self._should_retry_without_build_isolation(fallback_cmd, exc):
                raise
            self._run_local(
                self._inject_no_build_isolation(fallback_cmd),
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
        prefix_dir = python_bin.parent.parent
        env["VIRTUAL_ENV"] = str(prefix_dir)
        if (prefix_dir / "conda-meta").exists():
            env["CONDA_PREFIX"] = str(prefix_dir)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["DYNaforge_SANDBOX_ROOT"] = str(root_dir)
        if repo_dir is not None:
            env["DYNaforge_SANDBOX_REPO"] = str(repo_dir)
        cache_root = root_dir / ".cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        (cache_root / "matplotlib").mkdir(parents=True, exist_ok=True)
        (cache_root / "pip").mkdir(parents=True, exist_ok=True)
        (cache_root / "conda-pkgs").mkdir(parents=True, exist_ok=True)
        env.setdefault("XDG_CACHE_HOME", str(cache_root))
        env.setdefault("MPLCONFIGDIR", str(cache_root / "matplotlib"))
        env.setdefault("PIP_CACHE_DIR", str(cache_root / "pip"))
        env.setdefault("CONDA_PKGS_DIRS", str(cache_root / "conda-pkgs"))
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

    def _materialize_python_runtime(self, root_dir: Path, sandbox: SandboxSpec) -> Path:
        explicit_runtime = self._explicit_python_runtime(sandbox)
        if explicit_runtime is not None:
            return explicit_runtime
        desired_version = self._desired_python_version(sandbox)
        current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        if desired_version and desired_version != current_version:
            conda_cmd = shutil.which("conda")
            if conda_cmd is None:
                raise SandboxError(
                    f"Sandbox {sandbox.sandbox_id} requires Python {desired_version}, but no matching interpreter "
                    "or conda installation is available"
                )
            conda_env_dir = root_dir / f"conda-py{desired_version.replace('.', '')}"
            python_bin = self._python_bin(conda_env_dir)
            if not python_bin.exists():
                conda_cache_dir = root_dir / ".cache" / "conda-pkgs"
                conda_cache_dir.mkdir(parents=True, exist_ok=True)
                self._run_local(
                    [
                        conda_cmd,
                        "create",
                        "--solver",
                        "classic",
                        "-y",
                        "-p",
                        str(conda_env_dir),
                        f"python={desired_version}",
                        "pip",
                    ],
                    cwd=root_dir,
                    env={
                        **os.environ,
                        "CONDA_NO_PLUGINS": "true",
                        "CONDA_PKGS_DIRS": str(conda_cache_dir),
                    },
                    check=True,
                )
            return python_bin

        venv_dir = root_dir / "venv"
        if not venv_dir.exists():
            try:
                venv.EnvBuilder(with_pip=True).create(str(venv_dir))
            except Exception as exc:
                raise SandboxError(f"Failed to create virtualenv for sandbox {sandbox.sandbox_id}: {exc}") from exc
        return self._python_bin(venv_dir)

    @staticmethod
    def _desired_python_version(sandbox: SandboxSpec) -> Optional[str]:
        return LocalVenvSandboxRunner._python_runtime_spec(sandbox)

    @staticmethod
    def _python_runtime_identity(sandbox: SandboxSpec) -> Optional[str]:
        explicit = LocalVenvSandboxRunner._explicit_python_runtime_descriptor(sandbox)
        if explicit is not None:
            return explicit
        return LocalVenvSandboxRunner._python_runtime_spec(sandbox)

    @staticmethod
    def _python_runtime_spec(sandbox: SandboxSpec) -> Optional[str]:
        match = re.match(r"^python:(\d+\.\d+)", str(sandbox.image).strip())
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _explicit_python_runtime_descriptor(sandbox: SandboxSpec) -> Optional[str]:
        image = str(sandbox.image).strip()
        if image.startswith("python-env:") or image.startswith("conda-env:") or image.startswith("python-bin:"):
            return image
        return None

    @staticmethod
    def _explicit_python_runtime(sandbox: SandboxSpec) -> Optional[Path]:
        image = str(sandbox.image).strip()
        for prefix in ("python-env:", "conda-env:"):
            if image.startswith(prefix):
                env_dir = Path(image[len(prefix) :]).expanduser().resolve()
                python_bin = LocalVenvSandboxRunner._python_bin(env_dir)
                if not python_bin.exists():
                    raise SandboxError(f"Configured sandbox runtime does not exist: {python_bin}")
                return python_bin
        if image.startswith("python-bin:"):
            python_bin = Path(image[len("python-bin:") :]).expanduser().resolve()
            if not python_bin.exists():
                raise SandboxError(f"Configured sandbox python binary does not exist: {python_bin}")
            return python_bin
        return None

    @staticmethod
    def _rewrite_python_cmd(cmd: Sequence[str], python_bin: Path) -> List[str]:
        command = list(cmd)
        if command and command[0] in {"python", "python3", sys.executable}:
            command[0] = str(python_bin)
        return command

    @staticmethod
    def _should_retry_without_build_isolation(cmd: Sequence[str], error: SandboxError) -> bool:
        if not LocalVenvSandboxRunner._is_pip_like_command(cmd):
            return False
        lowered_cmd = [str(part).lower() for part in cmd]
        if "install" not in lowered_cmd:
            return False
        if "--no-build-isolation" in lowered_cmd:
            return False
        message = str(error).lower()
        return "build dependencies" in message or "setuptools" in message or "wheel" in message

    @staticmethod
    def _inject_no_build_isolation(cmd: Sequence[str]) -> List[str]:
        command = list(cmd)
        lowered = [str(part).lower() for part in command]
        try:
            install_index = lowered.index("install")
        except ValueError:
            return command
        command.insert(install_index + 1, "--no-build-isolation")
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
