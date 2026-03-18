from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from dynaforge.ir.schema import FailureType, NodeSpec
from dynaforge.runtime.reports import ExecutionCost, NodeExecutionResult

JsonDict = Dict[str, Any]

_PLACEHOLDER_RE = re.compile(r"{([^{}]+)}")


def execute_direct_sandbox_node(
    node: NodeSpec,
    inputs: Mapping[str, Any],
    context: Any,
) -> NodeExecutionResult:
    if node.sandbox is None:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error="Direct sandbox execution requires node.sandbox",
            trace={"live_direct_sandbox": True, "node_id": node.node_id},
        )

    meta = dict(node.meta)
    job_module = str(meta.get("job_module", "")).strip()
    command_template = meta.get("command_template")
    if not job_module and not command_template:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error="Direct sandbox execution requires node.meta.job_module or node.meta.command_template",
            trace={"live_direct_sandbox": True, "node_id": node.node_id},
        )

    request_dir = Path(context.artifact_store.root) / "direct_sandbox" / node.node_id
    request_dir.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex[:8]
    input_path = request_dir / f"{request_id}.input.json"
    output_path = request_dir / f"{request_id}.output.json"
    input_payload = {
        "node_id": node.node_id,
        "role": node.role,
        "description": node.description,
        "inputs": dict(inputs),
        "node_meta": meta,
    }
    input_path.write_text(json.dumps(input_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    if job_module:
        cmd = [
            "python",
            "-m",
            job_module,
            "--input-json",
            str(input_path),
            "--output-json",
            str(output_path),
        ]
        extra_args = meta.get("job_args", [])
        if isinstance(extra_args, Sequence) and not isinstance(extra_args, (str, bytes)):
            cmd.extend(str(item) for item in extra_args)
    else:
        cmd = _render_command_template(
            command_template,
            {
                "input_json": str(input_path),
                "output_json": str(output_path),
                "node": node.model_dump(mode="json"),
                "inputs": dict(inputs),
                "task": dict(inputs.get("task", {})) if isinstance(inputs.get("task", {}), Mapping) else {},
                "workflow_meta": dict(inputs.get("workflow_meta", {}))
                if isinstance(inputs.get("workflow_meta", {}), Mapping)
                else {},
            },
        )

    completed = context.sandbox_runner.run_in_sandbox(
        node.sandbox,
        cmd,
        timeout_s=int(node.sandbox.safety.timeout_s or 1800),
    )
    if completed.returncode != 0:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"Direct sandbox tool failed: {completed.stderr or completed.stdout}",
            trace={
                "live_direct_sandbox": True,
                "node_id": node.node_id,
                "job_module": job_module,
                "stdout_preview": (completed.stdout or "")[:500],
                "stderr_preview": (completed.stderr or "")[:500],
            },
            cost=ExecutionCost(tool_calls=1),
        )

    payload = _load_output_payload(output_path, completed.stdout)
    status = str(payload.get("status", "ok")).strip().lower()
    if status == "error":
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=str(payload.get("error") or payload.get("message") or "Direct sandbox job reported an error."),
            trace={
                "live_direct_sandbox": True,
                "node_id": node.node_id,
                "job_module": job_module,
                "payload_preview": payload,
            },
            cost=ExecutionCost(tool_calls=1),
        )

    outputs = payload.get("outputs")
    if not isinstance(outputs, Mapping):
        if isinstance(payload.get("result"), Mapping):
            outputs = dict(payload["result"])
        else:
            outputs = {"result": payload.get("result", payload)}
    else:
        outputs = dict(outputs)
    if "result" not in outputs:
        outputs["result"] = {
            key: value
            for key, value in outputs.items()
            if key != "result"
        }

    artifacts = []
    for artifact_payload in payload.get("artifacts", []) if isinstance(payload.get("artifacts"), list) else []:
        if not isinstance(artifact_payload, Mapping):
            continue
        artifact_path = str(artifact_payload.get("path", "")).strip()
        if artifact_path and Path(artifact_path).exists():
            artifacts.append(
                context.artifact_store.link_existing(
                    artifact_path,
                    mime=str(artifact_payload.get("mime", "")).strip() or None,
                )
            )

    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        for key, value in summary.items():
            if not str(key).endswith("_path"):
                continue
            artifact_path = str(value).strip()
            if artifact_path and Path(artifact_path).exists():
                artifacts.append(context.artifact_store.link_existing(artifact_path))

    trace: JsonDict = {
        "live_direct_sandbox": True,
        "node_id": node.node_id,
        "job_module": job_module,
    }
    if isinstance(payload.get("trace"), Mapping):
        trace.update(dict(payload["trace"]))

    selected_repo = outputs.get("selected_repo")
    if isinstance(selected_repo, str) and selected_repo.strip():
        trace["selected_repo"] = selected_repo.strip()
    selected_specialists = outputs.get("selected_specialists")
    if isinstance(selected_specialists, list):
        trace["selected_specialists"] = list(selected_specialists)

    confidence_value = payload.get("confidence", 0.82)
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        confidence = 0.82

    return NodeExecutionResult(
        outputs=dict(outputs),
        artifacts=artifacts,
        trace=trace,
        cost=ExecutionCost(tool_calls=1),
        confidence=max(0.0, min(confidence, 1.0)),
    )


def _load_output_payload(output_path: Path, stdout: str) -> JsonDict:
    if output_path.exists():
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping):
                return dict(payload)
        except (OSError, json.JSONDecodeError):
            pass
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Direct sandbox tool produced non-JSON stdout and no usable output_json artifact.") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Direct sandbox tool produced a non-object JSON payload.")
    return dict(payload)


def _render_command_template(template: Any, context: Mapping[str, Any]) -> list[str]:
    if not isinstance(template, Sequence) or isinstance(template, (str, bytes)):
        raise RuntimeError("command_template must be a sequence of strings")
    rendered: list[str] = []
    for item in template:
        if not isinstance(item, str):
            rendered.append(str(item))
            continue
        rendered.append(_PLACEHOLDER_RE.sub(lambda match: _resolve_placeholder(match.group(1), context), item))
    return rendered


def _resolve_placeholder(path: str, context: Mapping[str, Any]) -> str:
    current: Any = context
    for segment in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(segment)
        else:
            current = None
        if current is None:
            break
    if current is None:
        return ""
    if isinstance(current, (dict, list)):
        return json.dumps(current, ensure_ascii=True)
    return str(current)
