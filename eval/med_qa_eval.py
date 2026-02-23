from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import random
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from omegaconf import OmegaConf

from config import AppConfig
from core.contracts import TaskSpec
from methods.dispatcher import run_method
from models import build_model_routing, configure_openai, ensure_api_key, validate_backend
from run import to_app_config
from runtime import build_runtime
from utils import append_jsonl, log_event, set_log_config


def _load_dataset(
    split: str,
    cache_dir: str | None,
    config_name: str | None,
    data_dir: str | None,
    jsonl_dir: str | None,
):
    try:
        from datasets import load_dataset, load_from_disk
    except Exception as exc:
        raise RuntimeError("datasets is required. Install with: pip install datasets") from exc
    if jsonl_dir:
        return _load_jsonl_split(jsonl_dir, split)
    if data_dir:
        dataset = load_from_disk(data_dir)
        return dataset[split] if isinstance(dataset, dict) else dataset
    _assert_no_dataset_script()
    chosen = config_name or "bigbio_qa"
    try:
        return load_dataset("bigbio/med_qa", chosen, split=split, cache_dir=cache_dir)
    except Exception as exc:
        message = str(exc)
        if "Dataset scripts are no longer supported" in message:
            raise RuntimeError(
                "The dataset uses a loading script (med_qa.py). "
                "Please ask the dataset author to provide Parquet/Arrow files, "
                "or download locally and use --data-dir."
            ) from exc
        raise RuntimeError(
            "Unable to reach Hugging Face or dataset config missing. "
            "Try: set --config-name, set HF_ENDPOINT, or download locally and use --data-dir."
        ) from exc


def _auto_jsonl_dir(prefer_local: bool) -> str | None:
    if not prefer_local:
        return None
    candidate = ROOT / "data/med_qa_repo/data_clean/questions/US"
    if candidate.exists():
        return str(candidate)
    return None


def _assert_no_dataset_script() -> None:
    cache_root = Path("~/.cache/huggingface/hub").expanduser()
    dataset_root = cache_root / "datasets--bigbio--med_qa"
    if not dataset_root.exists():
        return
    snapshots = dataset_root / "snapshots"
    if not snapshots.exists():
        return
    for script in snapshots.glob("**/*.py"):
        if script.name == "med_qa.py":
            raise RuntimeError(
                "Detected dataset loading script (med_qa.py) in local cache. "
                "Please ask the dataset author to publish Parquet/Arrow files, "
                "or download locally and use --data-dir."
            )


def _extract_question(example: dict[str, Any]) -> str:
    for key in ("question", "question_text", "query", "prompt"):
        value = example.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(example.get("question", "")).strip()


def _extract_choices(example: dict[str, Any]) -> tuple[list[str], list[str]]:
    choices = example.get("choices") or example.get("options") or example.get("answer_choices")
    if isinstance(choices, dict):
        texts = choices.get("text") or choices.get("texts")
        labels = choices.get("label") or choices.get("labels")
        if texts:
            labels = labels or [chr(ord("A") + idx) for idx in range(len(texts))]
            return list(texts), list(labels)
        # MedQA JSONL uses letter keys with string values.
        if choices and all(isinstance(key, str) and len(key) == 1 for key in choices.keys()):
            labels = sorted(choices.keys())
            texts = [choices[label] for label in labels]
            return texts, labels
    if isinstance(choices, list):
        if choices and all(isinstance(item, dict) for item in choices):
            texts = []
            labels = []
            for item in choices:
                text = item.get("text") or item.get("content") or item.get("label") or ""
                label = item.get("label")
                texts.append(text)
                labels.append(label)
            if not any(labels):
                labels = [chr(ord("A") + idx) for idx in range(len(texts))]
            return texts, labels
        texts = [str(item) for item in choices]
        labels = [chr(ord("A") + idx) for idx in range(len(texts))]
        return texts, labels
    return [], []


def _extract_answer(example: dict[str, Any]) -> str:
    for key in ("answer", "answers", "label"):
        value = example.get(key)
        if isinstance(value, list) and value:
            return str(value[0]).strip()
        if isinstance(value, str) and value.strip():
            return value.strip()
    answer_idx = example.get("answer_idx")
    if isinstance(answer_idx, str) and answer_idx.strip():
        return answer_idx.strip()
    return ""


def _format_prompt(question: str, labels: list[str], choices: list[str]) -> str:
    lines = [f"Question: {question}", "Options:"]
    for label, text in zip(labels, choices):
        lines.append(f"{label}. {text}")
    lines.append("Answer with the option letter and text only.")
    return "\n".join(lines)


def _parse_prediction(text: str, labels: list[str], choices: list[str]) -> tuple[str | None, str | None]:
    cleaned = text.strip()
    for label in labels:
        if f"{label}." in cleaned or f"({label})" in cleaned or f" {label} " in cleaned:
            idx = labels.index(label)
            return label, choices[idx]
    lower = cleaned.lower()
    for label, choice in zip(labels, choices):
        if choice and choice.lower() in lower:
            return label, choice
    return None, None


def _gold_label_text(gold: str, labels: list[str], choices: list[str]) -> tuple[str | None, str | None]:
    if gold in labels:
        idx = labels.index(gold)
        return gold, choices[idx]
    for label, choice in zip(labels, choices):
        if gold and gold.lower() == choice.lower():
            return label, choice
    return None, None


def _extract_predicted_labels(text: str, labels: list[str], choices: list[str]) -> list[str]:
    if not text:
        return []
    label_set = {label.upper() for label in labels}
    found: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z])([A-Za-z])(?:[\\.)]|\\s)", text):
        label = match.group(1).upper()
        if label in label_set and label not in found:
            found.append(label)
    if not found:
        for match in re.finditer(r"\b([A-Za-z])\b", text):
            label = match.group(1).upper()
            if label in label_set and label not in found:
                found.append(label)
    lower = text.lower()
    for label, choice in zip(labels, choices):
        normalized_label = label.upper()
        if normalized_label in found:
            continue
        if choice and choice.lower() in lower:
            found.append(normalized_label)
    return found


def _extract_meta_info(example: dict[str, Any]) -> str:
    meta = example.get("meta_info")
    if isinstance(meta, str) and meta.strip():
        return meta.strip()
    return "unknown"


def _load_jsonl_split(jsonl_dir: str, split: str) -> list[dict[str, Any]]:
    path = Path(jsonl_dir) / f"{split}.jsonl"
    if not path.exists():
        raise RuntimeError(f"JSONL split not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


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


def _collect_usage(trace_path: Path, start_offset: int) -> tuple[dict[str, int], int]:
    if not trace_path.exists():
        return {"input": 0, "output": 0, "total": 0}, start_offset
    usage_sum = {"input": 0, "output": 0, "total": 0}
    with trace_path.open("r", encoding="utf-8") as handle:
        handle.seek(start_offset)
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = event.get("data") or {}
            usage = data.get("usage") or {}
            if isinstance(usage, dict):
                usage_sum["input"] += int(usage.get("input_tokens", 0) or 0)
                usage_sum["output"] += int(usage.get("output_tokens", 0) or 0)
                usage_sum["total"] += int(usage.get("total_tokens", 0) or 0)
        end_offset = handle.tell()
    return usage_sum, end_offset


async def run_eval(args: argparse.Namespace) -> None:
    cfg = OmegaConf.load(args.config)
    if args.overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(args.overrides))
    app_cfg: AppConfig = to_app_config(cfg)

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
        # Suppress noisy HTTP logs while we update a single progress line.
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
    validate_backend(app_cfg.model.backend)
    configure_openai(app_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(app_cfg.model)
    auto_jsonl_dir = _auto_jsonl_dir(args.prefer_local_jsonl)
    if args.jsonl_dir:
        jsonl_dir = args.jsonl_dir
    else:
        jsonl_dir = auto_jsonl_dir
    if jsonl_dir and args.progress:
        print(f"MedQA eval using JSONL dir: {jsonl_dir}")
    dataset = _load_dataset(args.split, args.cache_dir, args.config_name, args.data_dir, jsonl_dir)
    indices = list(range(len(dataset)))
    random.Random(args.seed).shuffle(indices)
    if args.max_samples:
        indices = indices[: args.max_samples]

    concurrency = max(1, int(args.concurrency))
    runtimes: list = []
    trace_paths: list[Path] = []
    trace_offsets: list[int] = []
    for worker_id in range(concurrency):
        worker_log_dir = (
            app_cfg.log_dir if concurrency == 1 else str(Path(app_cfg.log_dir) / f"medqa_worker_{worker_id}")
        )
        runtime = build_runtime(
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
        runtimes.append(runtime)
        trace_path = Path(runtime.observability.trace_path)
        trace_paths.append(trace_path)
        trace_offsets.append(trace_path.stat().st_size if trace_path.exists() else 0)

    total = 0
    correct = 0
    invalid = 0
    topk_hits = {3: 0, 5: 0}
    tokens_total = 0
    meta_metrics: dict[str, dict[str, int]] = {}
    failure_rows: list[dict[str, Any]] = []
    total_expected = len(indices)
    if not args.progress:
        log_event(
            "RUN",
            "start",
            "med_qa eval start",
            data={"split": args.split, "samples": total_expected, "concurrency": concurrency},
        )
    else:
        print(f"MedQA eval start | split={args.split} | samples={total_expected} | concurrency={concurrency}")

    index_queue: asyncio.Queue[int | None] = asyncio.Queue()
    for idx in indices:
        index_queue.put_nowait(idx)
    for _ in range(concurrency):
        index_queue.put_nowait(None)

    metrics_lock = asyncio.Lock()

    async def _process_sample(worker_id: int) -> None:
        nonlocal total, correct, invalid, tokens_total
        runtime = runtimes[worker_id]
        trace_path = trace_paths[worker_id]
        while True:
            idx = await index_queue.get()
            if idx is None:
                return
            row = dataset[idx]
            question = ""
            choices: list[str] = []
            labels: list[str] = []
            gold = ""
            answer = ""
            error_text: str | None = None
            meta = _extract_meta_info(row)
            try:
                question = _extract_question(row)
                choices, labels = _extract_choices(row)
                gold = _extract_answer(row)
                if not question or not choices:
                    raise ValueError("missing_question_or_choices")
                prompt = _format_prompt(question, labels, choices)
                task = TaskSpec(
                    goal=prompt,
                    constraints=["Answer with the option letter and text only."],
                    success_criteria=["Select exactly one of the provided options."],
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
                result = await run_method(app_cfg.method, task, runtime)
                answer = result.answer if result else ""
            except Exception as exc:  # pragma: no cover - defensive for eval runs
                answer = ""
                error_text = f"{type(exc).__name__}: {exc}"

            pred_labels = _extract_predicted_labels(answer, labels, choices)
            pred_label = pred_labels[0] if pred_labels else None
            pred_text = choices[labels.index(pred_label)] if pred_label in labels else None
            gold_label, gold_text = _gold_label_text(gold, labels, choices)
            ok = pred_label is not None and gold_label is not None and pred_label == gold_label
            is_invalid = pred_label is None
            usage_sum, trace_offsets[worker_id] = _collect_usage(trace_path, trace_offsets[worker_id])
            async with metrics_lock:
                invalid += 1 if is_invalid else 0
                for k in topk_hits.keys():
                    if gold_label and gold_label in pred_labels[:k]:
                        topk_hits[k] += 1
                tokens_total += usage_sum["total"]
                metrics = meta_metrics.setdefault(
                    meta, {"total": 0, "correct": 0, "invalid": 0, "top3": 0, "top5": 0, "tokens": 0}
                )
                metrics["total"] += 1
                metrics["correct"] += 1 if ok else 0
                metrics["invalid"] += 1 if is_invalid else 0
                metrics["top3"] += 1 if gold_label and gold_label in pred_labels[:3] else 0
                metrics["top5"] += 1 if gold_label and gold_label in pred_labels[:5] else 0
                metrics["tokens"] += usage_sum["total"]
                total += 1
                correct += 1 if ok else 0
                if args.progress:
                    _render_progress(total, total_expected)
                append_jsonl(
                    args.output,
                    {
                        "id": row.get("id", idx),
                        "question": question,
                        "choices": [{"label": l, "text": t} for l, t in zip(labels, choices)],
                        "gold": {"label": gold_label, "text": gold_text, "raw": gold},
                        "prediction": {"label": pred_label, "text": pred_text, "raw": answer},
                        "ok": ok,
                        "error": error_text,
                    },
                )
                if not ok:
                    failure_rows.append(
                        {
                            "id": row.get("id", idx),
                            "meta_info": meta,
                            "question": question,
                            "gold_label": gold_label,
                            "gold_text": gold_text,
                            "pred_label": pred_label,
                            "pred_text": pred_text,
                            "error": error_text or "",
                        }
                    )

    workers = [asyncio.create_task(_process_sample(worker_id)) for worker_id in range(concurrency)]
    await asyncio.gather(*workers)

    accuracy = correct / total if total else 0.0
    invalid_rate = invalid / total if total else 0.0
    avg_tokens = tokens_total / total if total else 0.0
    summary = {
        "split": args.split,
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "invalid": invalid,
        "invalid_rate": invalid_rate,
        "top3_accuracy": topk_hits[3] / total if total else 0.0,
        "top5_accuracy": topk_hits[5] / total if total else 0.0,
        "avg_total_tokens": avg_tokens,
        "meta_info": {},
    }
    for meta, metrics in meta_metrics.items():
        denom = metrics["total"] or 1
        summary["meta_info"][meta] = {
            "total": metrics["total"],
            "accuracy": metrics["correct"] / denom,
            "invalid_rate": metrics["invalid"] / denom,
            "top3_accuracy": metrics["top3"] / denom,
            "top5_accuracy": metrics["top5"] / denom,
            "avg_total_tokens": metrics["tokens"] / denom,
        }
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    if args.failures_csv:
        failures_path = Path(args.failures_csv)
        failures_path.parent.mkdir(parents=True, exist_ok=True)
        with failures_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "id",
                    "meta_info",
                    "question",
                    "gold_label",
                    "gold_text",
                    "pred_label",
                    "pred_text",
                    "error",
                ],
            )
            writer.writeheader()
            writer.writerows(failure_rows)
    if args.progress:
        sys.stdout.write("\n")
        sys.stdout.flush()
        print(f"MedQA eval complete | accuracy={accuracy:.4f} ({correct}/{total})")
    else:
        log_event("RUN", "done", "med_qa eval complete", data=summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Agent-Cop on MedQA (bigbio/med_qa).")
    parser.add_argument("--config", default="conf/config_w_api.yaml")
    parser.add_argument("--split", default="test")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--config-name", default=None, help="Dataset config name (default: bigbio_qa)")
    parser.add_argument("--data-dir", default=None, help="Use a locally saved dataset (load_from_disk).")
    parser.add_argument("--jsonl-dir", default=None, help="Use a local MedQA JSONL folder (e.g., data_clean/questions/US).")
    parser.add_argument(
        "--prefer-local-jsonl",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-use data/med_qa_repo/data_clean/questions/US when present.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show a single-line progress bar.",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", default="logs/med_qa_results.jsonl")
    parser.add_argument("--summary", default="logs/med_qa_summary.json")
    parser.add_argument("--failures-csv", default="logs/med_qa_failures.csv")
    parser.add_argument("overrides", nargs="*", help="Hydra-style overrides (key=value)")
    args = parser.parse_args()

    import asyncio

    asyncio.run(run_eval(args))


if __name__ == "__main__":
    main()
