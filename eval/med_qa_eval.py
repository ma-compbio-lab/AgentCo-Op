from __future__ import annotations

import argparse
import json
import random
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


def _load_dataset(split: str, cache_dir: str | None, config_name: str | None):
    try:
        from datasets import get_dataset_config_names, load_dataset
    except Exception as exc:
        raise RuntimeError(
            "datasets is required. Install with: pip install datasets"
        ) from exc
    configs = get_dataset_config_names("bigbio/med_qa")
    chosen = config_name or ("bigbio_qa" if "bigbio_qa" in configs else configs[0])
    return load_dataset("bigbio/med_qa", chosen, split=split, cache_dir=cache_dir)


def _extract_question(example: dict[str, Any]) -> str:
    for key in ("question", "question_text", "query", "prompt"):
        value = example.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(example.get("question", "")).strip()


def _extract_choices(example: dict[str, Any]) -> tuple[list[str], list[str]]:
    choices = example.get("choices") or example.get("options") or example.get("answer_choices")
    if isinstance(choices, dict):
        texts = choices.get("text") or choices.get("texts") or []
        labels = choices.get("label") or choices.get("labels") or []
        if texts and not labels:
            labels = [chr(ord("A") + idx) for idx in range(len(texts))]
        return list(texts), list(labels)
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


async def run_eval(args: argparse.Namespace) -> None:
    cfg = OmegaConf.load(args.config)
    if args.overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(args.overrides))
    app_cfg: AppConfig = to_app_config(cfg)

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
    runtime = build_runtime(
        routing,
        log_dir=app_cfg.log_dir,
        repair=app_cfg.repair,
        memory_cfg=app_cfg.memory,
        tool_cfg=app_cfg.tool,
        exec_cfg=app_cfg.exec,
        mcp_cfg=app_cfg.mcp,
    )

    dataset = _load_dataset(args.split, args.cache_dir, args.config_name)
    indices = list(range(len(dataset)))
    random.Random(args.seed).shuffle(indices)
    if args.max_samples:
        indices = indices[: args.max_samples]

    total = 0
    correct = 0
    log_event("RUN", "start", "med_qa eval start", data={"split": args.split, "samples": len(indices)})
    for idx in indices:
        row = dataset[idx]
        question = _extract_question(row)
        choices, labels = _extract_choices(row)
        gold = _extract_answer(row)
        if not question or not choices:
            continue
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
        pred_label, pred_text = _parse_prediction(answer, labels, choices)
        gold_label, gold_text = _gold_label_text(gold, labels, choices)
        ok = pred_label is not None and gold_label is not None and pred_label == gold_label
        total += 1
        correct += 1 if ok else 0
        append_jsonl(
            args.output,
            {
                "id": row.get("id", idx),
                "question": question,
                "choices": [{"label": l, "text": t} for l, t in zip(labels, choices)],
                "gold": {"label": gold_label, "text": gold_text, "raw": gold},
                "prediction": {"label": pred_label, "text": pred_text, "raw": answer},
                "ok": ok,
            },
        )

    accuracy = correct / total if total else 0.0
    summary = {"split": args.split, "total": total, "correct": correct, "accuracy": accuracy}
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    log_event("RUN", "done", "med_qa eval complete", data=summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Agent-Cop on MedQA (bigbio/med_qa).")
    parser.add_argument("--config", default="conf/config_w_api.yaml")
    parser.add_argument("--split", default="test")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--config-name", default=None, help="Dataset config name (default: bigbio_qa if present)")
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="logs/med_qa_results.jsonl")
    parser.add_argument("--summary", default="logs/med_qa_summary.json")
    parser.add_argument("overrides", nargs="*", help="Hydra-style overrides (key=value)")
    args = parser.parse_args()

    import asyncio

    asyncio.run(run_eval(args))


if __name__ == "__main__":
    main()
