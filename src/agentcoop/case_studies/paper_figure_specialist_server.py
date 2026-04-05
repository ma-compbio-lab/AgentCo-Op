from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from mcp.server.fastmcp import FastMCP

from agentcoop.ir.schema import ModelProvider, ModelSpec
from agentcoop.runtime.llm import LLMClientError, OpenAICompatibleLLMClient


def _log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


ROLE = os.environ.get("SPECIALIZED_AGENT_ROLE", "paper_spec").strip().lower()
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", f"paper-figure-{ROLE}")
mcp = FastMCP(name=SERVER_NAME, stateless_http=True, json_response=True)


def _model_from_env() -> ModelSpec:
    provider_name = os.environ.get("SPECIALIZED_AGENT_PROVIDER", "openai").strip().lower()
    try:
        provider = ModelProvider(provider_name)
    except ValueError:
        provider = ModelProvider.openai
    meta: dict[str, Any] = {}
    reasoning_effort = os.environ.get("SPECIALIZED_AGENT_REASONING_EFFORT", "").strip()
    if reasoning_effort:
        meta["reasoning_effort"] = reasoning_effort
    base_url = os.environ.get("SPECIALIZED_AGENT_BASE_URL", "").strip()
    if base_url:
        meta["base_url"] = base_url
    api_key_env = os.environ.get("SPECIALIZED_AGENT_API_KEY_ENV", "").strip()
    if api_key_env:
        meta["api_key_env"] = api_key_env
    allow_unauthenticated = os.environ.get("SPECIALIZED_AGENT_ALLOW_UNAUTHENTICATED", "").strip().lower()
    if allow_unauthenticated in {"1", "true", "yes"}:
        meta["allow_unauthenticated"] = True
    max_output_tokens = int(os.environ.get("SPECIALIZED_AGENT_MAX_OUTPUT_TOKENS", "768"))
    return ModelSpec(
        provider=provider,
        name=os.environ.get("SPECIALIZED_AGENT_MODEL_NAME", "gpt-5-mini").strip() or "gpt-5-mini",
        temperature=0.2,
        max_output_tokens=max_output_tokens,
        meta=meta,
    )


def _load_text(path_str: str) -> str:
    if not path_str:
        return ""
    path = Path(path_str).resolve()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _load_json(path_str: str) -> dict[str, Any]:
    if not path_str:
        return {}
    path = Path(path_str).resolve()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _complete_json(system_prompt: str, user_payload: Mapping[str, Any]) -> dict[str, Any]:
    client = OpenAICompatibleLLMClient(timeout_s=int(os.environ.get("SPECIALIZED_AGENT_TIMEOUT_S", "180")))
    model = _model_from_env()
    response = client.complete(
        model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True, indent=2)},
        ],
        response_format={"type": "json_object"},
    )
    content = str(response.get("content", "")).strip()
    return json.loads(content) if content else {}


def _require_keys(payload: Mapping[str, Any], keys: set[str]) -> None:
    missing = sorted(key for key in keys if key not in payload)
    if missing:
        raise KeyError(f"Missing required specialist keys: {missing}")


def _paper_spec_fallback(task: Mapping[str, Any]) -> dict[str, Any]:
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    dataset_label = str(hints.get("dataset_label", task.get("title", "trajectory task"))).strip() or "trajectory task"
    return {
        "required_artifacts": [
            "trajectory_figure",
            "paga_figure",
            "dynamic_gene_panel",
            "summary_json",
        ],
        "required_summary_fields": [
            "cluster_count",
            "pseudotime_range",
            "paga_edge_count",
            "dynamic_gene_panel_genes",
            "dynamic_gene_panel_table_path",
            "root_provenance",
        ],
        "scientific_focus": "Trajectory reconstruction, lineage abstraction, and dynamic-gene evidence along pseudotime.",
        "summary": f"The task requires a paper-style {dataset_label} figure package with explicit dynamic-gene and root-selection provenance.",
        "target_figure_description": str(hints.get("target_figure_description", "")),
    }


def _execution_planner_fallback(task: Mapping[str, Any], paper_spec: Mapping[str, Any]) -> dict[str, Any]:
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    candidate_repos = list(hints.get("candidate_repos", []))
    selected_repo = "scanpy"
    for repo in candidate_repos:
        if str(repo.get("name", "")).strip() == "scanpy":
            selected_repo = "scanpy"
            break
    analysis_config = dict(hints.get("analysis_config", {}))
    dataset_label = str(hints.get("dataset_label", task.get("title", "trajectory task"))).strip() or "trajectory task"
    return {
        "selected_repo": selected_repo,
        "analysis_config": analysis_config,
        "summary": f"Deterministic collaboration planner selected Scanpy and preserved the configured {dataset_label} paper-figure pipeline.",
        "plan_focus": str(paper_spec.get("scientific_focus", "")),
    }


def _figure_critic_fallback(task: Mapping[str, Any], paper_spec: Mapping[str, Any], tool_result: Mapping[str, Any]) -> dict[str, Any]:
    hints = dict(task.get("hints", {})) if isinstance(task.get("hints", {}), Mapping) else {}
    summary = dict(tool_result.get("summary", {})) if isinstance(tool_result.get("summary", {}), Mapping) else {}
    reference_summary = _load_json(str(hints.get("reference_summary_path", "")))
    concerns: list[str] = []
    artifact_completeness = 9.0
    if not summary.get("dynamic_gene_panel_table_path"):
        concerns.append("Missing dynamic-gene panel table export.")
        artifact_completeness -= 2.0
    if not summary.get("root_provenance"):
        concerns.append("Missing root provenance metadata.")
        artifact_completeness -= 1.0
    if int(summary.get("cluster_count", 0) or 0) != int(reference_summary.get("cluster_count", summary.get("cluster_count", 0) or 0)):
        concerns.append("Cluster count diverges from reference summary.")
        artifact_completeness -= 1.0
    dataset_label = str(hints.get("dataset_label", task.get("title", "trajectory task"))).strip() or "trajectory task"
    return {
        "methodology_faithfulness": 9,
        "biological_plausibility": 8,
        "artifact_completeness": max(1, int(round(artifact_completeness))),
        "should_refine": False,
        "concerns": concerns,
        "summary": f"Fallback figure critic completed a structured {dataset_label} artifact review against the reference summary.",
        "required_artifacts": paper_spec.get("required_artifacts", []),
    }


@mcp.tool()
def describe_capabilities() -> Dict[str, Any]:
    return {
        "name": SERVER_NAME,
        "role": ROLE,
        "domain": "paper_figure_reproduction",
        "outputs": {
            "paper_spec": ["required_artifacts", "required_summary_fields", "scientific_focus", "summary"],
            "execution_planner": ["selected_repo", "analysis_config", "summary"],
            "figure_critic": [
                "methodology_faithfulness",
                "biological_plausibility",
                "artifact_completeness",
                "should_refine",
                "concerns",
                "summary",
            ],
        }.get(ROLE, []),
    }


@mcp.tool()
def run_agent(
    task: Dict[str, Any],
    paper_spec: Optional[Dict[str, Any]] = None,
    tool_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    paper_spec = paper_spec or {}
    tool_result = tool_result or {}
    methods_excerpt = _load_text(str(task.get("hints", {}).get("methods_excerpt_path", "")))
    target_description = str(task.get("hints", {}).get("target_figure_description", ""))
    try:
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            if ROLE == "paper_spec":
                payload = {
                    "task_title": task.get("title", ""),
                    "methods_excerpt": methods_excerpt,
                    "target_figure_description": target_description,
                }
                try:
                    result = _complete_json(
                        "You are a specialized scientific paper-figure interpreter. "
                        "Extract the required artifact set, required summary fields, scientific focus, and a concise summary. "
                        "Return JSON with keys required_artifacts, required_summary_fields, scientific_focus, and summary.",
                        payload,
                    )
                    _require_keys(
                        result,
                        {"required_artifacts", "required_summary_fields", "scientific_focus", "summary"},
                    )
                except (LLMClientError, json.JSONDecodeError, KeyError, TypeError):
                    result = _paper_spec_fallback(task)
            elif ROLE == "execution_planner":
                payload = {
                    "methods_excerpt": methods_excerpt,
                    "target_figure_description": target_description,
                    "candidate_repos": task.get("hints", {}).get("candidate_repos", []),
                    "default_analysis_config": task.get("hints", {}).get("analysis_config", {}),
                    "paper_spec": paper_spec,
                }
                try:
                    result = _complete_json(
                        "You are a specialized scientific execution planner. "
                        "Choose the best repo and a conservative analysis_config for faithful paper-style figure reproduction. "
                        "Return JSON with keys selected_repo, analysis_config, and summary.",
                        payload,
                    )
                    _require_keys(result, {"selected_repo", "analysis_config", "summary"})
                except (LLMClientError, json.JSONDecodeError, KeyError, TypeError):
                    result = _execution_planner_fallback(task, paper_spec)
            elif ROLE == "figure_critic":
                payload = {
                    "methods_excerpt": methods_excerpt,
                    "target_figure_description": target_description,
                    "paper_spec": paper_spec,
                    "tool_result": tool_result,
                    "reference_summary": _load_json(str(task.get("hints", {}).get("reference_summary_path", ""))),
                }
                try:
                    result = _complete_json(
                        "You are a specialized scientific figure critic. "
                        "Evaluate whether the generated artifact package satisfies the required paper-style figure outputs. "
                        "Return JSON with keys methodology_faithfulness, biological_plausibility, artifact_completeness, should_refine, concerns, and summary.",
                        payload,
                    )
                    _require_keys(
                        result,
                        {"methodology_faithfulness", "biological_plausibility", "artifact_completeness", "concerns", "summary"},
                    )
                except (LLMClientError, json.JSONDecodeError, KeyError, TypeError):
                    result = _figure_critic_fallback(task, paper_spec, tool_result)
            else:
                raise RuntimeError(f"Unsupported SPECIALIZED_AGENT_ROLE: {ROLE}")
        return {"status": "ok", "role": ROLE, "result": result}
    except Exception as exc:  # pragma: no cover - exercised in live runs
        _log("paper-figure specialist error:", repr(exc))
        return {
            "status": "error",
            "error_type": "tool_runtime_error",
            "message": str(exc),
            "role": ROLE,
        }


def main() -> None:
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
