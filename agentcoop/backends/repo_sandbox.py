"""Sandbox-repo backend — **command builder only**.

During the framework phase we do not actually `docker run`. Instead this
backend validates the manifest, builds the exact command string, and
returns it as output. The experiment phase can swap in a `_run_docker`
implementation without touching callers.

The generated command enforces the safety checklist from
`instructions.md` §6.5 and §13.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcoop.core.schema import NodeResult, NodeSpec
from agentcoop.backends.base import NodeContext


@dataclass
class RepoSandboxBackend:
    name: str = "sandbox_repo"
    execute_live: bool = False  # framework-only: False → dry-run, return command

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult:
        start = time.monotonic()
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            return NodeResult(
                node_id=node.node_id,
                ok=False,
                output={},
                errors=["sandbox_repo: missing 'manifest' dict in payload"],
            )
        try:
            cmd = build_run_command(
                manifest=manifest,
                inputs_dir=payload.get("inputs_dir", "./inputs"),
                outputs_dir=payload.get("outputs_dir", "./outputs"),
                request_path=payload.get("request_path", "/inputs/request.json"),
                result_path=payload.get("result_path", "/outputs/result.json"),
            )
        except ValueError as exc:
            return NodeResult(
                node_id=node.node_id,
                ok=False,
                output={},
                errors=[f"sandbox_repo: {exc}"],
                latency_s=time.monotonic() - start,
            )

        if not self.execute_live:
            return NodeResult(
                node_id=node.node_id,
                ok=True,
                output={
                    "mode": "dry_run",
                    "command": cmd,
                    "manifest_digest": _digest(manifest),
                },
                confidence=1.0,
                logs_summary="dry-run: repo sandbox command built but not executed",
                latency_s=time.monotonic() - start,
            )

        # Live execution intentionally not wired in the framework phase.
        return NodeResult(
            node_id=node.node_id,
            ok=False,
            output={"command": cmd},
            errors=["sandbox_repo live execution is not wired in framework phase"],
            latency_s=time.monotonic() - start,
        )


def build_run_command(
    *,
    manifest: dict[str, Any],
    inputs_dir: str,
    outputs_dir: str,
    request_path: str,
    result_path: str,
) -> list[str]:
    required = ("image", "commit_sha")
    for k in required:
        if not manifest.get(k):
            raise ValueError(f"manifest missing required field '{k}'")
    if not manifest.get("non_root", True):
        raise ValueError("manifest must run as non-root user")

    network = manifest.get("network", "none")
    cpus = str(manifest.get("cpus", 4))
    memory = f"{manifest.get('memory_gb', 16)}g"
    pids = str(manifest.get("pids_limit", 512))
    tmpfs_size = f"{manifest.get('tmpfs_gb', 2)}g"

    inputs_abs = str(Path(inputs_dir).resolve())
    outputs_abs = str(Path(outputs_dir).resolve())

    cmd: list[str] = [
        "docker", "run", "--rm",
        "--network", network,
        "--cpus", cpus,
        "--memory", memory,
        "--pids-limit", pids,
        "--read-only",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}",
        "-v", f"{inputs_abs}:/inputs:ro",
        "-v", f"{outputs_abs}:/outputs:rw",
    ]

    for k in manifest.get("secrets", []):
        cmd += ["-e", f"{k}"]  # value injected by env, never interpolated here

    image = manifest["image"]
    if "digest" in manifest:
        image = f"{image}@{manifest['digest']}"
    cmd.append(image)
    cmd += ["--input", request_path, "--output", result_path]
    return cmd


def _digest(manifest: dict[str, Any]) -> str:
    import hashlib
    blob = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


__all__ = ["RepoSandboxBackend", "build_run_command"]
