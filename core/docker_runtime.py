from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any

from core.contracts import ContainerLimits, ToolExecutionResult, ToolPlan
from core.hooks import HookManager
from core.observability import Observability
from utils import ensure_dir, log_event


class DockerRuntime:
    def __init__(
        self,
        *,
        log_dir: str,
        run_dir: str,
        default_base_image: str,
        default_limits: dict[str, Any] | None = None,
        build_allow_net: bool = True,
        run_allow_net: bool = False,
        observability: Observability | None = None,
        hooks: HookManager | None = None,
    ) -> None:
        self.log_dir = log_dir
        self.run_dir = run_dir
        self.default_base_image = default_base_image
        self.default_limits = default_limits or {}
        self.build_allow_net = build_allow_net
        self.run_allow_net = run_allow_net
        self.obs = observability
        self.hooks = hooks
        ensure_dir(run_dir)

    def execute(self, plan: ToolPlan, *, run_id: str) -> ToolExecutionResult:
        if not shutil.which("docker"):
            log_event("TOOLS", "docker", "docker not available", level="warn")
            return ToolExecutionResult(status="fail", error_signature="docker_not_found")

        run_root = os.path.join(self.run_dir, run_id)
        ensure_dir(run_root)
        outputs_dir = os.path.join(run_root, "outputs")
        ensure_dir(outputs_dir)

        context_dir, dockerfile_path = self._resolve_container_paths(plan, run_root)
        if not os.path.isdir(context_dir):
            log_event(
                "TOOLS",
                "docker_context",
                "docker context missing",
                level="warn",
                data={"context_dir": context_dir},
            )
            return ToolExecutionResult(status="fail", error_signature="context_missing")
        image_tag = self._image_tag(plan, run_id)

        build_result = self._build_image(
            image_tag=image_tag,
            dockerfile_path=dockerfile_path,
            context_dir=context_dir,
            log_dir=run_root,
            build_args=plan.container_spec.build_args,
            allow_net=plan.container_spec.build_allow_net and self.build_allow_net,
        )
        if build_result is not None:
            return build_result

        commands = list(plan.run_commands) + list(plan.verify_commands)
        if not commands:
            return ToolExecutionResult(status="fail", error_signature="no_commands")

        workdir = plan.container_spec.workdir or "/workspace"
        run_script = self._write_run_script(run_root, commands, workdir=workdir)
        return self._run_container(
            image_tag=image_tag,
            run_root=run_root,
            run_script=run_script,
            outputs_dir=outputs_dir,
            limits=self._merge_limits(plan.container_spec.limits),
            allow_net=plan.container_spec.run_allow_net and self.run_allow_net,
            env=plan.container_spec.env,
            workdir=workdir,
        )

    def _resolve_container_paths(self, plan: ToolPlan, run_root: str) -> tuple[str, str]:
        container_spec = plan.container_spec
        context_dir = container_spec.context_dir
        dockerfile_path = container_spec.dockerfile_path

        if dockerfile_path:
            dockerfile_path = self._resolve_path(dockerfile_path)
            if not context_dir:
                context_dir = os.path.dirname(dockerfile_path)

        if not context_dir:
            context_dir = os.path.join(run_root, "context")
            ensure_dir(context_dir)
        else:
            context_dir = self._resolve_path(context_dir)

        if not dockerfile_path:
            dockerfile_path = os.path.join(context_dir, "Dockerfile")
            self._write_default_dockerfile(dockerfile_path, plan)

        return context_dir, dockerfile_path

    def _write_default_dockerfile(self, path: str, plan: ToolPlan) -> None:
        base_image = plan.container_spec.base_image or self.default_base_image
        lines = [f"FROM {base_image}", "WORKDIR /workspace"]
        if plan.install_strategy == "pip" and plan.selected_tool.kind in {"pypi", "cli"}:
            package = plan.selected_tool.name
            if plan.selected_tool.version:
                package = f"{package}=={plan.selected_tool.version}"
            lines.append(f"RUN pip install --no-cache-dir {package}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _build_image(
        self,
        *,
        image_tag: str,
        dockerfile_path: str,
        context_dir: str,
        log_dir: str,
        build_args: dict[str, str],
        allow_net: bool,
    ) -> ToolExecutionResult | None:
        cmd = [
            "docker",
            "build",
            "-t",
            image_tag,
            "-f",
            dockerfile_path,
        ]
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        if not allow_net:
            cmd.extend(["--network", "none"])
        cmd.append(context_dir)
        log_event("TOOLS", "docker_build", "building docker image", data={"tag": image_tag})
        start = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        elapsed = time.time() - start
        if self.obs:
            self.obs.event(
                "docker_build",
                {"tag": image_tag, "returncode": proc.returncode, "elapsed_s": elapsed},
            )
        if proc.returncode == 0:
            return None
        stdout_path, stderr_path = self._write_logs(log_dir, proc.stdout, proc.stderr, prefix="build")
        if self.hooks:
            self.hooks.on_tool_error(None, None, {}, RuntimeError("docker_build_failed"))
        return ToolExecutionResult(
            status="fail",
            stdout=proc.stdout,
            stderr=proc.stderr,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error_signature="docker_build_failed",
            cost={"build_time_s": elapsed},
        )

    def _run_container(
        self,
        *,
        image_tag: str,
        run_root: str,
        run_script: str,
        outputs_dir: str,
        limits: ContainerLimits,
        allow_net: bool,
        env: dict[str, str],
        workdir: str,
    ) -> ToolExecutionResult:
        container_name = f"{image_tag}-run"
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--workdir",
            workdir,
            "-v",
            f"{run_root}:/agent",
            "-v",
            f"{outputs_dir}:/outputs",
        ]
        if not allow_net:
            cmd.extend(["--network", "none"])
        if limits.cpus is not None:
            cmd.extend(["--cpus", str(limits.cpus)])
        if limits.memory_mb is not None:
            cmd.extend(["--memory", f"{limits.memory_mb}m"])
        if limits.pids is not None:
            cmd.extend(["--pids-limit", str(limits.pids)])
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([image_tag, "bash", run_script])

        log_event("TOOLS", "docker_run", "running tool container", data={"tag": image_tag})
        start = time.time()
        timeout_s = limits.timeout_s
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s if timeout_s else None,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, check=False)
            return ToolExecutionResult(status="fail", error_signature="timeout")
        elapsed = time.time() - start
        if self.obs:
            self.obs.event(
                "docker_run",
                {"tag": image_tag, "returncode": proc.returncode, "elapsed_s": elapsed},
            )

        stdout_path, stderr_path = self._write_logs(run_root, proc.stdout, proc.stderr, prefix="run")
        status = "success" if proc.returncode == 0 else "fail"
        if status == "fail" and self.hooks:
            self.hooks.on_tool_error(None, None, {}, RuntimeError("docker_run_failed"))
        artifacts = self._collect_artifacts(outputs_dir)
        return ToolExecutionResult(
            status=status,
            stdout=proc.stdout,
            stderr=proc.stderr,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            artifacts=artifacts,
            error_signature=None if status == "success" else "docker_run_failed",
            cost={"run_time_s": elapsed},
        )

    @staticmethod
    def _write_run_script(run_root: str, commands: list[str], *, workdir: str) -> str:
        path = os.path.join(run_root, "run.sh")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\nset -e\n")
            f.write(f"cd {workdir}\n")
            for command in commands:
                f.write(command)
                f.write("\n")
        return "/agent/run.sh"

    @staticmethod
    def _collect_artifacts(outputs_dir: str) -> list[str]:
        artifacts: list[str] = []
        for root, _dirs, files in os.walk(outputs_dir):
            for name in files:
                full_path = os.path.join(root, name)
                artifacts.append(full_path)
        return artifacts

    @staticmethod
    def _write_logs(base_dir: str, stdout: str, stderr: str, *, prefix: str) -> tuple[str, str]:
        stdout_path = os.path.join(base_dir, f"{prefix}_stdout.txt")
        stderr_path = os.path.join(base_dir, f"{prefix}_stderr.txt")
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write(stdout or "")
        with open(stderr_path, "w", encoding="utf-8") as f:
            f.write(stderr or "")
        return stdout_path, stderr_path

    @staticmethod
    def _resolve_path(path: str) -> str:
        return os.path.abspath(path)

    def _merge_limits(self, limits: ContainerLimits) -> ContainerLimits:
        return ContainerLimits(
            cpus=limits.cpus if limits.cpus is not None else self.default_limits.get("cpus"),
            memory_mb=limits.memory_mb if limits.memory_mb is not None else self.default_limits.get("memory_mb"),
            pids=limits.pids if limits.pids is not None else self.default_limits.get("pids"),
            timeout_s=limits.timeout_s if limits.timeout_s is not None else self.default_limits.get("timeout_s"),
        )

    @staticmethod
    def _image_tag(plan: ToolPlan, run_id: str) -> str:
        name = plan.selected_tool.name or "tool"
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).lower()
        suffix = run_id[:8]
        return f"agent_cop_{safe}_{suffix}"
