from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPATIAL_ROOT = ROOT / "third_party" / "spatialbench"
if SPATIAL_ROOT.exists() and str(SPATIAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SPATIAL_ROOT))

from omegaconf import OmegaConf

from config import (
    AppConfig,
    ChatConfig,
    ExecConfig,
    LogConfig,
    MCPConfig,
    MemoryConfig,
    ModelConfig,
    PlanningConfig,
    RepairConfig,
    TaskConfig,
    ToolConfig,
)
from core.contracts import TaskSpec
from utils import append_jsonl, log_event, set_log_config


def _to_app_config(cfg) -> AppConfig:
    cfg_obj = OmegaConf.to_object(cfg)
    if isinstance(cfg_obj, AppConfig):
        return cfg_obj
    if isinstance(cfg_obj, dict):
        task_dict = cfg_obj.get("task", {})
        model_dict = cfg_obj.get("model", {})
        log_dict = cfg_obj.get("log", {})
        repair_dict = cfg_obj.get("repair", {})
        memory_dict = cfg_obj.get("memory", {})
        planning_dict = cfg_obj.get("planning", {})
        tool_dict = cfg_obj.get("tool", {})
        exec_dict = cfg_obj.get("exec", {})
        mcp_dict = cfg_obj.get("mcp", {})
        chat_dict = cfg_obj.get("chat", {})
        return AppConfig(
            task=TaskConfig(**task_dict),
            method=cfg_obj.get("method", "orchestrated"),
            model=ModelConfig(**model_dict),
            log_dir=cfg_obj.get("log_dir", "logs"),
            log=LogConfig(**log_dict) if isinstance(log_dict, dict) else LogConfig(),
            repair=RepairConfig(**repair_dict) if isinstance(repair_dict, dict) else RepairConfig(),
            memory=MemoryConfig(**memory_dict) if isinstance(memory_dict, dict) else MemoryConfig(),
            planning=PlanningConfig(**planning_dict) if isinstance(planning_dict, dict) else PlanningConfig(),
            tool=ToolConfig(**tool_dict) if isinstance(tool_dict, dict) else ToolConfig(),
            exec=ExecConfig(**exec_dict) if isinstance(exec_dict, dict) else ExecConfig(),
            mcp=MCPConfig(**mcp_dict) if isinstance(mcp_dict, dict) else MCPConfig(),
            chat=ChatConfig(**chat_dict) if isinstance(chat_dict, dict) else ChatConfig(),
        )
    raise TypeError("Config is not compatible with AppConfig.")


def _require_spatialbench():
    try:
        from spatialbench.types import TestCase
        from spatialbench.graders import GRADER_REGISTRY
        from spatialbench.harness.utils import download_data, setup_workspace, cleanup_workspace
    except Exception as exc:
        raise RuntimeError(
            "SpatialBench is not importable. Ensure third_party/spatialbench is present "
            "and install its dependencies if required (see third_party/spatialbench/pyproject.toml)."
        ) from exc
    return TestCase, GRADER_REGISTRY, download_data, setup_workspace, cleanup_workspace


def _render_progress(current: int, total: int, width: int = 30) -> None:
    if total <= 0:
        return
    ratio = min(max(current / total, 0), 1)
    filled = int(width * ratio)
    if current >= total:
        bar = "=" * width
    else:
        bar = "=" * max(filled - 1, 0) + ">" + " " * (width - filled)
    percent = int(ratio * 100)
    line = f"[{bar}] {current}/{total} ({percent}%)"
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


def _load_eval_paths(eval_path: str | None, eval_dir: str | None) -> list[Path]:
    if eval_path:
        path = Path(eval_path)
        if not path.exists():
            raise RuntimeError(f"Eval file not found: {path}")
        return [path]
    directory = Path(eval_dir or SPATIAL_ROOT / "evals_canonical")
    if not directory.exists():
        raise RuntimeError(f"Eval directory not found: {directory}")
    paths = [path for path in directory.rglob("*.json") if path.is_file()]
    return sorted(paths)


def _parse_json_answer(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


async def run_eval(args: argparse.Namespace) -> None:
    TestCase, GRADER_REGISTRY, download_data, setup_workspace, cleanup_workspace = _require_spatialbench()
    cfg = OmegaConf.load(args.config)
    if args.overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(args.overrides))
    app_cfg: AppConfig = _to_app_config(cfg)

    if args.progress:
        set_log_config(
            enabled=False,
            level=app_cfg.log.level,
            use_color=app_cfg.log.use_color,
            use_icons=app_cfg.log.use_icons,
            preview_chars=app_cfg.log.preview_chars,
            show_prompts=app_cfg.log.show_prompts,
            show_outputs=app_cfg.log.show_outputs,
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("openai.agents").setLevel(logging.WARNING)
    else:
        set_log_config(
            enabled=app_cfg.log.enabled,
            level=app_cfg.log.level,
            use_color=app_cfg.log.use_color,
            use_icons=app_cfg.log.use_icons,
            preview_chars=app_cfg.log.preview_chars,
            show_prompts=app_cfg.log.show_prompts,
            show_outputs=app_cfg.log.show_outputs,
        )

    eval_paths = _load_eval_paths(args.eval_path, args.eval_dir)
    if args.max_samples:
        eval_paths = eval_paths[: args.max_samples]

    total_expected = len(eval_paths)
    if args.dry_run:
        summary = {"total": total_expected, "dry_run": True}
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"SpatialBench dry-run complete | samples={total_expected}")
        return

    from methods.dispatcher import run_method
    from models import build_model_routing, configure_openai, ensure_api_key, validate_backend
    from runtime import build_runtime

    validate_backend(app_cfg.model.backend)
    configure_openai(app_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(app_cfg.model)
    runtime = build_runtime(
        routing,
        log_dir=app_cfg.log_dir,
        repair=app_cfg.repair,
        memory_cfg=app_cfg.memory,
        planning_cfg=app_cfg.planning,
        tool_cfg=app_cfg.tool,
        exec_cfg=app_cfg.exec,
        mcp_cfg=app_cfg.mcp,
    )
    if not args.progress:
        log_event("RUN", "start", "spatialbench eval start", data={"samples": total_expected})
    else:
        print(f"SpatialBench eval start | samples={total_expected}")

    results = []
    passed = 0
    invalid = 0
    durations: list[float] = []

    for idx, eval_path in enumerate(eval_paths, 1):
        eval_data = json.loads(eval_path.read_text())
        test_case = TestCase(**eval_data)
        work_dir = setup_workspace(test_case.id, args.run_id)
        contextual_data = []
        if not args.skip_download and test_case.data_node:
            try:
                contextual_data = download_data(test_case.data_node, work_dir)
            except Exception as exc:
                cleanup_workspace(work_dir, keep=args.keep_workspace)
                if args.continue_on_error:
                    results.append(
                        {
                            "id": test_case.id,
                            "eval_path": str(eval_path),
                            "passed": False,
                            "error": f"download_failed: {exc}",
                        }
                    )
                    invalid += 1
                    if args.progress:
                        _render_progress(idx, total_expected)
                    continue
                raise

        data_context = ""
        if contextual_data:
            data_context = (
                "\n\nHere is the context of the selected nodes the user would like to use: "
                f"<ContextualNodeData>{json.dumps(contextual_data)}</ContextualNodeData>"
            )

        task_prompt = (
            f"{test_case.task}\n\n"
            "IMPORTANT:\n"
            "1) Write your final answer as a JSON object to a file named `eval_answer.json`.\n"
            "2) The file should contain ONLY the JSON object with the required fields.\n"
            "3) After writing the file, the task is complete.\n"
            "Example eval_answer.json:\n"
            "{\n  \"field1\": value1,\n  \"field2\": value2\n}\n"
            f"{data_context}\n"
            f"Workspace path: {work_dir}"
        )

        task = TaskSpec(
            goal=task_prompt,
            constraints=[
                "Return a JSON object only (no markdown).",
                "Write eval_answer.json to the provided workspace path.",
            ],
            success_criteria=[
                "JSON fields must match the task description.",
            ],
            budget_tokens=app_cfg.task.budget_tokens,
            input_modalities=app_cfg.task.input_modalities,
            output_modalities=app_cfg.task.output_modalities,
            task_type=app_cfg.task.task_type,
            prompt_verbosity=app_cfg.task.prompt_verbosity,
            allow_web_search_for_spec=app_cfg.task.allow_web_search_for_spec,
            spec_max_search_queries=app_cfg.task.spec_max_search_queries,
            allow_tool_search=app_cfg.task.allow_tool_search,
            tool_max_candidates=app_cfg.task.tool_max_candidates,
            tool_max_search_queries=app_cfg.task.tool_max_search_queries,
        )

        start = time.monotonic()
        result = await run_method(app_cfg.method, task, runtime)
        duration = time.monotonic() - start
        durations.append(duration)

        answer_text = result.answer if result else ""
        parsed = _parse_json_answer(answer_text)
        eval_answer_path = work_dir / "eval_answer.json"
        if parsed is not None:
            eval_answer_path.write_text(json.dumps(parsed), encoding="utf-8")

        grader_result = None
        is_invalid = parsed is None
        if test_case.grader and parsed is not None:
            grader_type = test_case.grader.get("type")
            grader_config = test_case.grader.get("config", {})
            grader_cls = GRADER_REGISTRY.get(grader_type)
            if grader_cls:
                grader = grader_cls()
                grader_result = grader.evaluate_answer(parsed, grader_config)
        ok = bool(grader_result.passed) if grader_result else False
        passed += 1 if ok else 0
        invalid += 1 if is_invalid else 0
        results.append(
            {
                "id": test_case.id,
                "eval_path": str(eval_path),
                "passed": ok,
                "invalid": is_invalid,
                "duration_s": duration,
                "grader_metrics": getattr(grader_result, "metrics", None) if grader_result else None,
                "grader_reasoning": getattr(grader_result, "reasoning", None) if grader_result else None,
            }
        )
        append_jsonl(args.output, results[-1])
        cleanup_workspace(work_dir, keep=args.keep_workspace)

        if args.progress:
            _render_progress(idx, total_expected)

    pass_rate = passed / total_expected if total_expected else 0.0
    invalid_rate = invalid / total_expected if total_expected else 0.0
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    summary = {
        "total": total_expected,
        "passed": passed,
        "pass_rate": pass_rate,
        "invalid": invalid,
        "invalid_rate": invalid_rate,
        "avg_duration_s": avg_duration,
        "results_path": args.output,
    }

    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    if args.progress:
        sys.stdout.write("\n")
        sys.stdout.flush()
        print(f"SpatialBench eval complete | pass_rate={pass_rate:.4f} ({passed}/{total_expected})")
    else:
        log_event("RUN", "done", "spatialbench eval complete", data=summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Agent-Cop on SpatialBench.")
    parser.add_argument("--config", default="conf/config_w_api.yaml")
    parser.add_argument("--eval-path", default=None)
    parser.add_argument("--eval-dir", default=None)
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show a single-line progress bar.",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--output", default="logs/spatialbench_results.jsonl")
    parser.add_argument("--summary", default="logs/spatialbench_summary.json")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without running the workflow.")
    parser.add_argument("overrides", nargs="*", help="Hydra-style overrides (key=value)")
    args = parser.parse_args()

    import asyncio

    asyncio.run(run_eval(args))


if __name__ == "__main__":
    main()
