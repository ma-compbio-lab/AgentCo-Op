"""Deterministic graders for the six AFlow-aligned benchmarks.

Each grader exposes:
    grade(prediction: str | dict, reference: Any, task: BenchmarkTask | None = None) -> dict

Return shape:
    {
        "ok": bool,
        "score": float,        # the metric value
        "metric": str,         # e.g. "solve_rate", "f1", "pass@1"
        "confidence": float,   # 0..1 — used by reviewer clamp
        "normalized_prediction": str,
        "normalized_reference": Any,
        "issues": list[str],
    }
"""

from __future__ import annotations

import collections
import re
import string
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# GSM8K — final numeric answer after "####" in reference; extract from pred.
# ---------------------------------------------------------------------------


_GSM8K_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _extract_gsm8k_number(text: str) -> str | None:
    if text is None:
        return None
    # Prefer the last number in the text — GSM8K convention.
    matches = _GSM8K_NUMBER_RE.findall(text)
    if not matches:
        return None
    last = matches[-1].replace(",", "")
    try:
        f = float(last)
        return str(int(f)) if f.is_integer() else str(f)
    except ValueError:
        return last


def grade_gsm8k(prediction: Any, reference: Any, task: Any = None) -> dict:
    pred_s = prediction.get("final_answer", "") if isinstance(prediction, dict) else str(prediction)
    ref_s = str(reference)
    pred_num = _extract_gsm8k_number(pred_s)
    ref_num = _extract_gsm8k_number(ref_s) or ref_s.replace(",", "").strip()
    ok = pred_num is not None and pred_num == ref_num
    return {
        "ok": ok,
        "score": 1.0 if ok else 0.0,
        "metric": "solve_rate",
        "confidence": 1.0 if ok else 0.0,
        "normalized_prediction": pred_num,
        "normalized_reference": ref_num,
        "issues": [] if ok else ["gsm8k_mismatch"],
    }


# ---------------------------------------------------------------------------
# MATH — extract \boxed{...}; if sympy available, compare via equivalence.
# ---------------------------------------------------------------------------


_BOXED_RE = re.compile(r"\\boxed{([^}]*)}")


def _extract_boxed(text: str) -> str | None:
    if not text:
        return None
    # Handle nested braces: walk from rightmost \boxed{
    idx = text.rfind(r"\boxed{")
    if idx == -1:
        m = _BOXED_RE.search(text)
        return m.group(1).strip() if m else None
    depth = 0
    i = idx + len(r"\boxed{")
    start = i
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return text[start:i].strip()
            depth -= 1
        i += 1
    return None


def _math_canonical(expr: str | None) -> str:
    if expr is None:
        return ""
    s = expr.strip()
    s = s.replace(" ", "").replace("\\,", "").replace("\\!", "")
    s = s.replace("\\left", "").replace("\\right", "")
    s = s.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    return s


def grade_math(prediction: Any, reference: Any, task: Any = None) -> dict:
    pred_s = prediction.get("final_answer", "") if isinstance(prediction, dict) else str(prediction)
    ref_raw = str(reference)
    pred = _extract_boxed(pred_s) or pred_s.strip()
    ref = _extract_boxed(ref_raw) or ref_raw.strip()

    # Canonicalization first.
    canon_pred = _math_canonical(pred)
    canon_ref = _math_canonical(ref)
    ok = canon_pred == canon_ref

    # Try sympy-backed equivalence if available (optional).
    if not ok:
        try:
            from sympy import Symbol, simplify  # noqa: F401
            from sympy.parsing.latex import parse_latex  # type: ignore

            pp = parse_latex(pred)
            rr = parse_latex(ref)
            if pp is not None and rr is not None and simplify(pp - rr) == 0:
                ok = True
        except Exception:
            pass

    return {
        "ok": ok,
        "score": 1.0 if ok else 0.0,
        "metric": "solve_rate",
        "confidence": 1.0 if ok else 0.0,
        "normalized_prediction": canon_pred,
        "normalized_reference": canon_ref,
        "issues": [] if ok else ["math_answer_mismatch"],
    }


# ---------------------------------------------------------------------------
# HumanEval / MBPP — run the reference test harness in a subprocess sandbox.
# ---------------------------------------------------------------------------


_SANDBOX_TIMEOUT_S = 20


def _run_python(code: str, timeout: int = _SANDBOX_TIMEOUT_S) -> tuple[int, str, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "-I", path],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    finally:
        Path(path).unlink(missing_ok=True)


def grade_humaneval(prediction: Any, reference: Any, task: Any = None) -> dict:
    """`reference = {test, entry_point, canonical_solution}`; `prediction` is full code or {"code": ...}."""
    ref = reference if isinstance(reference, dict) else {}
    test = ref.get("test", "")
    entry = ref.get("entry_point", "")
    pred_code = prediction.get("code", "") if isinstance(prediction, dict) else str(prediction or "")
    prompt = ""
    if task is not None and hasattr(task, "prompt"):
        prompt = task.prompt

    # The HumanEval harness expects the prompt+completion to be imported,
    # then calls `check(<entry_point>)`.
    harness = (
        pred_code
        + "\n\n"
        + test
        + f"\n\ncheck({entry})\n"
    )
    if entry and entry not in pred_code and entry not in prompt:
        # Programmer may have returned only the body; try prepending prompt.
        harness = prompt + pred_code + "\n\n" + test + f"\n\ncheck({entry})\n"

    rc, _stdout, stderr = _run_python(harness)
    ok = rc == 0
    return {
        "ok": ok,
        "score": 1.0 if ok else 0.0,
        "metric": "pass@1",
        "confidence": 1.0 if ok else 0.0,
        "normalized_prediction": pred_code[:200] + ("..." if len(pred_code) > 200 else ""),
        "normalized_reference": f"check({entry})",
        "issues": [] if ok else [f"returncode={rc}", stderr[-400:]],
    }


def grade_mbpp(prediction: Any, reference: Any, task: Any = None) -> dict:
    ref = reference if isinstance(reference, dict) else {}
    test_list = ref.get("test_list", [])
    setup = ref.get("test_setup_code", "")
    pred_code = prediction.get("code", "") if isinstance(prediction, dict) else str(prediction or "")
    harness = pred_code + "\n\n" + setup + "\n\n" + "\n".join(test_list) + "\n"
    rc, _stdout, stderr = _run_python(harness)
    ok = rc == 0
    return {
        "ok": ok,
        "score": 1.0 if ok else 0.0,
        "metric": "pass@1",
        "confidence": 1.0 if ok else 0.0,
        "normalized_prediction": pred_code[:200] + ("..." if len(pred_code) > 200 else ""),
        "normalized_reference": test_list,
        "issues": [] if ok else [f"returncode={rc}", stderr[-400:]],
    }


# ---------------------------------------------------------------------------
# HotpotQA — token F1 (SQuAD-style normalization).
# ---------------------------------------------------------------------------


def _normalize_answer(s: str) -> str:
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def _f1(pred: str, ref: str) -> float:
    pt = _normalize_answer(pred).split()
    rt = _normalize_answer(ref).split()
    if not pt and not rt:
        return 1.0
    if not pt or not rt:
        return 0.0
    common = collections.Counter(pt) & collections.Counter(rt)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pt)
    recall = num_same / len(rt)
    return 2 * precision * recall / (precision + recall)


def grade_hotpotqa(prediction: Any, reference: Any, task: Any = None) -> dict:
    pred_s = prediction.get("final_answer", "") if isinstance(prediction, dict) else str(prediction)
    ref_s = str(reference)
    f1 = _f1(pred_s, ref_s)
    em = 1.0 if _normalize_answer(pred_s) == _normalize_answer(ref_s) else 0.0
    return {
        "ok": f1 >= 0.5,
        "score": f1,
        "metric": "f1",
        "confidence": f1,
        "normalized_prediction": _normalize_answer(pred_s),
        "normalized_reference": _normalize_answer(ref_s),
        "issues": [] if f1 > 0 else ["no_token_overlap"],
        "em": em,
    }


# ---------------------------------------------------------------------------
# DROP — official numeric-aware F1 + EM, supporting multi-span and dates.
# Simplified port; matches the official behavior on the common cases.
# ---------------------------------------------------------------------------


_ARTICLES_RE = re.compile(r"\b(a|an|the)\b")


def _drop_normalize(text: str) -> str:
    text = text.lower()
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    text = _ARTICLES_RE.sub(" ", text)
    return " ".join(text.split())


def _drop_extract_answers(reference: Any) -> list[list[str]]:
    """DROP references are nested: {"spans": [...], "types": [...]} or lists.

    We accept either: a list[str] of answers, a dict with "spans", or a list
    of dicts (multiple gold answers).
    """
    if isinstance(reference, list):
        return [[str(x) for x in reference]]
    if isinstance(reference, dict):
        if "spans" in reference and reference["spans"]:
            return [[str(x) for x in reference["spans"]]]
        if "number" in reference and reference["number"]:
            return [[str(reference["number"])]]
        if "date" in reference and reference["date"]:
            d = reference["date"]
            date_str = " ".join(filter(None, [d.get("day", ""), d.get("month", ""), d.get("year", "")]))
            return [[date_str.strip()]]
    return [[str(reference)]]


def grade_drop(prediction: Any, reference: Any, task: Any = None) -> dict:
    pred_s = prediction.get("final_answer", "") if isinstance(prediction, dict) else str(prediction)
    pred_items = [s.strip() for s in re.split(r"[;,]", pred_s) if s.strip()] or [pred_s]
    candidates = _drop_extract_answers(reference)
    best_em, best_f1 = 0.0, 0.0
    for gold in candidates:
        em = 1.0 if sorted([_drop_normalize(x) for x in pred_items]) == sorted([_drop_normalize(x) for x in gold]) else 0.0
        f1s = [_f1(" ".join(pred_items), " ".join(gold))]
        f1 = max(f1s) if f1s else 0.0
        best_em = max(best_em, em)
        best_f1 = max(best_f1, f1)
    return {
        "ok": best_f1 >= 0.5,
        "score": best_f1,
        "metric": "drop_f1",
        "confidence": best_f1,
        "normalized_prediction": _drop_normalize(pred_s),
        "normalized_reference": candidates,
        "em": best_em,
        "issues": [] if best_f1 > 0 else ["drop_no_overlap"],
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


GRADERS = {
    "gsm8k": grade_gsm8k,
    "math": grade_math,
    "humaneval": grade_humaneval,
    "mbpp": grade_mbpp,
    "hotpotqa": grade_hotpotqa,
    "drop": grade_drop,
}


def grade(dataset: str, prediction: Any, reference: Any, task: Any = None) -> dict:
    if dataset not in GRADERS:
        raise KeyError(f"no grader for '{dataset}'; have {sorted(GRADERS)}")
    return GRADERS[dataset](prediction, reference, task)


__all__ = ["grade", "GRADERS"]
