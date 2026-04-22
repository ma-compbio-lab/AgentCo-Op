"""Python sandbox backend.

Runs model-provided Python in a child process, with a wall-clock timeout
and captured stdout/stderr. Does **not** hand over real OS isolation — a
real sandbox would use Docker / nsjail / seccomp. We surface that as a
warning in the result.
"""

from __future__ import annotations

import asyncio
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Any

from agentcoop.core.schema import NodeResult, NodeSpec
from agentcoop.backends.base import NodeContext


@dataclass
class PythonSandboxBackend:
    name: str = "python_sandbox"
    default_timeout_s: int = 30

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult:
        code = payload.get("code", "")
        if not isinstance(code, str) or not code:
            return NodeResult(
                node_id=node.node_id,
                ok=False,
                output={},
                errors=["python_sandbox: missing 'code' in payload"],
            )
        timeout = int(payload.get("timeout_s", node.timeout_s or self.default_timeout_s))
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return NodeResult(
                    node_id=node.node_id,
                    ok=False,
                    output={},
                    errors=[f"python_sandbox: timeout after {timeout}s"],
                    latency_s=time.monotonic() - start,
                )
            latency = time.monotonic() - start
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            ok = proc.returncode == 0
            return NodeResult(
                node_id=node.node_id,
                ok=ok,
                output={"stdout": stdout, "stderr": stderr, "returncode": proc.returncode},
                errors=[] if ok else [f"exit={proc.returncode}", stderr[-500:]],
                latency_s=latency,
                metrics={"returncode": proc.returncode},
                logs_summary=_tail(stdout, 500),
            )
        except FileNotFoundError as exc:
            return NodeResult(
                node_id=node.node_id,
                ok=False,
                output={},
                errors=[f"python_sandbox: executable missing: {exc}"],
                latency_s=time.monotonic() - start,
            )


def _tail(text: str, n: int) -> str:
    return text[-n:] if len(text) > n else text


__all__ = ["PythonSandboxBackend"]
