from __future__ import annotations

from methods.base import MethodResult


def basic_metrics(result: MethodResult) -> dict[str, float | int]:
    answer_len = len(result.answer or "")
    ok = 1 if result.judge_report and result.judge_report.ok else 0
    score = result.judge_report.score if result.judge_report else 0.0
    return {"answer_len": answer_len, "ok": ok, "score": score}
