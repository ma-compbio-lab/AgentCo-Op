from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPATIAL_ROOT = ROOT / "third_party" / "spatialbench"
if SPATIAL_ROOT.exists() and str(SPATIAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SPATIAL_ROOT))

from config import (
    AdaptiveConfig,
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
        adaptive_dict = cfg_obj.get("adaptive", {})
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
            adaptive=AdaptiveConfig(**adaptive_dict) if isinstance(adaptive_dict, dict) else AdaptiveConfig(),
        )
    raise TypeError("Config is not compatible with AppConfig.")


def _require_spatialbench():
    try:
        from spatialbench import EvalRunner
    except Exception as exc:
        raise RuntimeError(
            "SpatialBench is not importable. Ensure third_party/spatialbench exists and "
            "install it first: `pip install -e third_party/spatialbench`."
        ) from exc
    return EvalRunner


def _collect_data_nodes(eval_paths: list[Path]) -> list[str]:
    # Parse eval files once and pre-collect external dataset URIs to avoid
    # concurrent cache/download races inside per-sample runner threads.
    from spatialbench.types import TestCase

    uris: set[str] = set()
    for path in eval_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            test_case = TestCase(**payload)
        except Exception:
            continue
        node = test_case.data_node
        if isinstance(node, list):
            for item in node:
                if isinstance(item, str) and item:
                    uris.add(item)
        elif isinstance(node, str) and node:
            uris.add(node)
    return sorted(uris)


def _load_eval_paths(eval_path: str | None, eval_dir: str | None) -> list[Path]:
    if eval_path:
        path = Path(eval_path)
        if not path.exists():
            raise FileNotFoundError(f"Eval file not found: {path}")
        return [path]

    root = Path(eval_dir) if eval_dir else (SPATIAL_ROOT / "evals_canonical")
    if not root.exists():
        raise FileNotFoundError(f"Eval directory not found: {root}")

    # `manifest.json` is metadata, not an eval.
    paths = [p for p in root.rglob("*.json") if p.is_file() and p.name.lower() != "manifest.json"]
    return sorted(paths)


def _parse_json_answer(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(raw[start : end + 1])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _render_progress(current: int, total: int, width: int = 30) -> None:
    if total <= 0:
        return
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(width * ratio)
    if current >= total:
        bar = "=" * width
    else:
        bar = "=" * max(filled - 1, 0) + ">" + " " * (width - filled)
    percent = int(ratio * 100)
    line = f"[{bar}] {current}/{total} ({percent}%)"
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


def _build_task(task_prompt: str, work_dir: Path, app_cfg) -> TaskSpec:
    task_cfg = app_cfg.task
    prompt = (
        f"{task_prompt}\n\n"
        f"Workspace path: {work_dir}\n"
        "You MUST write eval_answer.json into this workspace directory.\n"
        "The final answer must be a valid JSON object matching the required fields."
    )
    return TaskSpec(
        goal=prompt,
        constraints=task_cfg.constraints
        + [
            "Return a JSON object only (no markdown wrapper).",
            "Write the same JSON object to eval_answer.json in the provided workspace path.",
        ],
        success_criteria=task_cfg.success_criteria + ["Fields satisfy the eval task schema."],
        budget_tokens=task_cfg.budget_tokens,
        input_modalities=task_cfg.input_modalities,
        output_modalities=task_cfg.output_modalities,
        task_type=task_cfg.task_type,
        prompt_verbosity=task_cfg.prompt_verbosity,
        allow_web_search_for_spec=task_cfg.allow_web_search_for_spec,
        spec_max_search_queries=task_cfg.spec_max_search_queries,
        allow_tool_search=task_cfg.allow_tool_search,
        tool_max_candidates=task_cfg.tool_max_candidates,
        tool_max_search_queries=task_cfg.tool_max_search_queries,
    )


def _build_agent_function(app_cfg, runtime):
    from methods.dispatcher import run_method

    def agent_function(task_prompt: str, work_dir: Path):
        task = _build_task(task_prompt, work_dir, app_cfg)
        result = asyncio.run(run_method(app_cfg.method, task, runtime))
        answer_text = result.answer if result else ""

        parsed = _parse_json_answer(answer_text)
        answer_file = work_dir / "eval_answer.json"

        # If model output is not directly parseable, honor existing eval_answer.json if already written.
        if parsed is None and answer_file.exists():
            try:
                existing = json.loads(answer_file.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    parsed = existing
            except Exception:
                parsed = None

        if parsed is not None:
            answer_file.write_text(json.dumps(parsed, ensure_ascii=True), encoding="utf-8")

        metadata = {
            "workflow_method": app_cfg.method,
            "task_id": task.task_id,
        }
        if result and result.judge_report is not None:
            metadata["judge_ok"] = result.judge_report.ok
            metadata["judge_score"] = result.judge_report.score

        return {"answer": parsed, "metadata": metadata}

    return agent_function


def run_eval(args: argparse.Namespace) -> None:
    EvalRunner = _require_spatialbench()

    cfg = OmegaConf.load(args.config)
    if args.overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(args.overrides))
    app_cfg = _to_app_config(cfg)

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
    total = len(eval_paths)
    concurrency = max(1, int(args.concurrency))
    run_id = args.run_id or f"agentcop_{int(time.time())}_{uuid4().hex[:8]}"

    output_path = Path(args.output)
    if output_path.exists() and not args.append_output:
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        summary = {
            "total": total,
            "dry_run": True,
            "concurrency": concurrency,
            "eval_dir": args.eval_dir,
            "eval_path": args.eval_path,
            "run_id": run_id,
        }
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"SpatialBench dry-run complete | samples={total}")
        return

    from models import build_model_routing, configure_openai, ensure_api_key, validate_backend
    from runtime import build_runtime

    validate_backend(app_cfg.model.backend)
    configure_openai(app_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(app_cfg.model)

    if args.prefetch_download:
        data_nodes = _collect_data_nodes(eval_paths)
        latch_nodes = [node for node in data_nodes if node.startswith("latch://")]
        if latch_nodes and shutil.which("latch") is None:
            raise RuntimeError(
                "SpatialBench requires `latch` CLI for latch:// datasets. "
                "Install and login first (e.g., `conda run -n spatialbench latch login`)."
            )
        if data_nodes:
            from spatialbench.harness.utils import batch_download_datasets

            if args.progress:
                print(f"SpatialBench prefetch | datasets={len(data_nodes)}")
            try:
                with contextlib.redirect_stdout(io.StringIO()) if args.progress else contextlib.nullcontext():
                    with contextlib.redirect_stderr(io.StringIO()) if args.progress else contextlib.nullcontext():
                        batch_download_datasets(data_nodes, show_progress=not args.progress)
            except Exception as exc:
                raise RuntimeError(
                    "SpatialBench dataset prefetch failed. "
                    "Check latch auth/network and retry, or disable prefetch with --no-prefetch-download."
                ) from exc

    def _build_runtime_for_worker(worker_id: int):
        worker_log_dir = (
            app_cfg.log_dir if concurrency == 1 else str(Path(app_cfg.log_dir) / f"spatialbench_worker_{worker_id}")
        )
        return build_runtime(
            routing,
            log_dir=worker_log_dir,
            repair=app_cfg.repair,
            memory_cfg=app_cfg.memory,
            planning_cfg=app_cfg.planning,
            tool_cfg=app_cfg.tool,
            exec_cfg=app_cfg.exec,
            mcp_cfg=app_cfg.mcp,
            adaptive_cfg=app_cfg.adaptive,
        )

    worker_local = threading.local()
    worker_counter = {"value": 0}
    worker_lock = threading.Lock()

    def _init_worker() -> None:
        with worker_lock:
            worker_id = worker_counter["value"]
            worker_counter["value"] += 1
        runtime = _build_runtime_for_worker(worker_id)
        worker_local.agent_function = _build_agent_function(app_cfg, runtime)

    def _run_single_eval(eval_path: Path) -> dict[str, Any]:
        started = time.monotonic()
        output_capture = ""
        try:
            runner = EvalRunner(eval_path, keep_workspace=args.keep_workspace, run_id=run_id)
            agent_function = getattr(worker_local, "agent_function", None)
            if agent_function is None:
                runtime = _build_runtime_for_worker(0)
                agent_function = _build_agent_function(app_cfg, runtime)

            if args.progress:
                out_buf = io.StringIO()
                err_buf = io.StringIO()
                with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                    result = runner.run(agent_function=agent_function)
                output_capture = (out_buf.getvalue() + "\n" + err_buf.getvalue()).strip()
            else:
                result = runner.run(agent_function=agent_function)

            duration = time.monotonic() - started
            grader_result = result.get("grader_result")
            agent_answer = result.get("agent_answer")
            is_invalid = not isinstance(agent_answer, dict)
            is_passed = bool(getattr(grader_result, "passed", result.get("passed")))
            return {
                "run_id": run_id,
                "id": result.get("test_id", eval_path.stem),
                "eval_path": str(eval_path),
                "passed": is_passed,
                "invalid": is_invalid,
                "duration_s": duration,
                "grader_metrics": _json_safe(getattr(grader_result, "metrics", None)),
                "grader_reasoning": getattr(grader_result, "reasoning", None),
                "agent_answer": _json_safe(agent_answer),
                "metadata": _json_safe(result.get("metadata", {})),
                "runner_output_tail": output_capture[-500:].strip() if output_capture else None,
            }
        except Exception as exc:
            if not args.continue_on_error:
                raise
            duration = time.monotonic() - started
            error_text = f"{type(exc).__name__}: {exc}"
            if output_capture:
                tail = output_capture[-500:].strip()
                if tail:
                    error_text = f"{error_text} | runner_output_tail={tail}"
            return {
                "run_id": run_id,
                "id": eval_path.stem,
                "eval_path": str(eval_path),
                "passed": False,
                "invalid": True,
                "duration_s": duration,
                "error": error_text,
            }

    if not args.progress:
        log_event(
            "RUN",
            "start",
            "spatialbench eval start",
            data={"samples": total, "method": app_cfg.method, "concurrency": concurrency},
        )
    else:
        print(f"SpatialBench eval start | samples={total} | concurrency={concurrency}")

    passed = 0
    invalid = 0
    durations: list[float] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=concurrency, initializer=_init_worker) as executor:
        future_map = {executor.submit(_run_single_eval, eval_path): eval_path for eval_path in eval_paths}
        for future in as_completed(future_map):
            completed += 1
            eval_path = future_map[future]
            try:
                row = future.result()
            except Exception as exc:
                if not args.continue_on_error:
                    raise
                row = {
                    "id": eval_path.stem,
                    "eval_path": str(eval_path),
                    "passed": False,
                    "invalid": True,
                    "duration_s": 0.0,
                    "error": f"{type(exc).__name__}: {exc}",
                }

            append_jsonl(str(output_path), row)
            if row.get("duration_s") is not None:
                durations.append(float(row["duration_s"]))
            if row.get("passed"):
                passed += 1
            if row.get("invalid"):
                invalid += 1
            if args.progress:
                _render_progress(completed, total)

    pass_rate = (passed / total) if total else 0.0
    invalid_rate = (invalid / total) if total else 0.0
    avg_duration = (sum(durations) / len(durations)) if durations else 0.0
    summary = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": pass_rate,
        "invalid": invalid,
        "invalid_rate": invalid_rate,
        "avg_duration_s": avg_duration,
        "method": app_cfg.method,
        "concurrency": concurrency,
        "run_id": run_id,
        "prefetch_download": bool(args.prefetch_download),
        "results_path": str(output_path),
    }

    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")

    if args.progress:
        sys.stdout.write("\n")
        sys.stdout.flush()
        print(f"SpatialBench eval complete | pass_rate={pass_rate:.4f} ({passed}/{total})")
    else:
        log_event("RUN", "done", "spatialbench eval complete", data=summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Agent-Cop workflow on SpatialBench evals.")
    parser.add_argument("--config", default="conf/config_w_api.yaml")
    parser.add_argument("--eval-path", default=None, help="Single SpatialBench eval JSON path.")
    parser.add_argument(
        "--eval-dir",
        default=str(SPATIAL_ROOT / "evals_canonical"),
        help="Directory containing SpatialBench eval JSON files.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show single-line sample progress.",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=1, help="Number of parallel eval workers.")
    parser.add_argument("--output", default="logs/spatialbench_results.jsonl")
    parser.add_argument("--summary", default="logs/spatialbench_summary.json")
    parser.add_argument("--run-id", default=None, help="Workspace run_id forwarded to SpatialBench EvalRunner.")
    parser.add_argument(
        "--prefetch-download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prefetch all datasets once before parallel execution to avoid cache races.",
    )
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--append-output", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("overrides", nargs="*", help="Hydra-style overrides (key=value)")
    args = parser.parse_args()
    run_eval(args)


if __name__ == "__main__":
    main()
