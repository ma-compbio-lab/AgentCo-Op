"""PREFERENCE-level checks: what did human experts actually prefer?

This module has exactly one rule and it is absolute: **it never synthesizes a
preference.** There is no model fallback, no rubric, no heuristic tie-break
that invents a winner. If no expert recorded a judgment, the answer is
``UNAVAILABLE``.

The temptation to do otherwise is strong, because a preference score is the
one number that would make an open-ended task look benchmarkable. Yielding to
it is how a system ends up reporting that its output was "preferred" by an
evaluator that is a copy of the generator. Hence two further rules:

* judgments produced by a model are **excluded**, not down-weighted — this
  check is named ``expert_pairwise`` and a model is not an expert;
* judgments where the judge is the subject are **excluded** as self-judgment,
  which is the reviewer/generator correlation ``SignalSource`` exists to
  detect.

Judgments are read from recorded data only: ``params['judgments']``, a JSONL
file named by ``params['judgments_path']``, or
``dossier.resources['expert_judgments']``. Nothing here touches a network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from agentcoop.evaluate.contract import CheckContext, make_result, unavailable
from agentcoop.ir.checks import CheckLevel, CheckResult, CheckStatus, SignalSource

_LEVEL = CheckLevel.PREFERENCE

#: Judge kinds that are not experts. Recorded judgments carrying one of these
#: are dropped before any counting happens.
_MODEL_JUDGE_KINDS = frozenset({"llm", "model", "llm_judge", "ai", "rubric"})

_WINNER_KEYS = ("winner", "preferred", "chosen")
_LOSER_KEYS = ("loser", "rejected", "dispreferred")


def _load_judgments(ctx: CheckContext) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return ``(judgments, error)`` from recorded sources only."""
    supplied = ctx.params.get("judgments")
    if supplied is not None:
        return [dict(j) for j in supplied if isinstance(j, dict)], None

    path = ctx.params.get("judgments_path")
    if path:
        file = Path(str(path))
        if not file.exists():
            return [], f"judgment file '{file}' does not exist"
        rows: list[dict[str, Any]] = []
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return [], f"judgment file '{file}' contains a malformed line"
            if isinstance(row, dict):
                rows.append(row)
        return rows, None

    from_resources = ctx.dossier.resources.get("expert_judgments")
    if isinstance(from_resources, list):
        return [dict(j) for j in from_resources if isinstance(j, dict)], None

    return [], None


def _side(row: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_model_judge(row: dict[str, Any]) -> bool:
    for key in ("judge_kind", "judge_type", "source"):
        value = row.get(key)
        if isinstance(value, str) and value.strip().lower() in _MODEL_JUDGE_KINDS:
            return True
    return False


def expert_pairwise(ctx: CheckContext) -> CheckResult:
    """How often experts preferred this subject over an admissible alternative.

    Statuses, and why each one:

    ``UNAVAILABLE``
        no recorded expert judgment mentions this subject. This is the common
        case for open-ended scientific tasks and must not be papered over.
    ``INCONCLUSIVE``
        judgments exist but too few (``params['min_judgments']``, default 3)
        or from too few distinct experts (``params['min_judges']``, default 1)
        for the evaluator to be trusted with a verdict.
    ``PASS`` / ``FAIL``
        the win rate is at or below ``params['min_win_rate']`` (default 0.5).
    """
    subject = ctx.subject or ctx.params.get("subject")
    if not subject and ctx.workflow is not None:
        subject = ctx.workflow.workflow_id
    if not subject:
        return unavailable(
            _LEVEL,
            "no subject to judge; expert preference is defined over named candidates",
            source=SignalSource.HUMAN,
        )

    judgments, error = _load_judgments(ctx)
    if error:
        return unavailable(_LEVEL, error, source=SignalSource.HUMAN, subject=subject)
    if not judgments:
        return unavailable(
            _LEVEL,
            "no recorded expert judgments; preference is unmeasured for this task",
            source=SignalSource.HUMAN,
            subject=subject,
        )

    excluded_model = 0
    excluded_self = 0
    excluded_task = 0
    wins = 0
    losses = 0
    judges: set[str] = set()
    opponents: set[str] = set()

    for row in judgments:
        task_id = row.get("task_id")
        if isinstance(task_id, str) and task_id and task_id != ctx.dossier.task_id:
            excluded_task += 1
            continue
        if _is_model_judge(row):
            excluded_model += 1
            continue
        winner = _side(row, _WINNER_KEYS)
        loser = _side(row, _LOSER_KEYS)
        if winner is None or loser is None or winner == loser:
            continue
        if subject not in (winner, loser):
            continue
        judge = row.get("judge")
        if isinstance(judge, str) and judge == subject:
            excluded_self += 1
            continue
        if isinstance(judge, str) and judge:
            judges.add(judge)
        if winner == subject:
            wins += 1
            opponents.add(loser)
        else:
            losses += 1
            opponents.add(winner)

    n = wins + losses
    evidence: dict[str, Any] = {
        "subject_id": subject,
        "wins": wins,
        "losses": losses,
        "n_judgments": n,
        "n_judges": len(judges),
        "opponents": sorted(opponents),
        "excluded_model_judgments": excluded_model,
        "excluded_self_judgments": excluded_self,
        "excluded_other_task": excluded_task,
    }

    if n == 0:
        reason = "no recorded expert judgment involves this subject"
        if excluded_model:
            reason += (
                f" ({excluded_model} model-produced judgment(s) excluded: a model "
                "is not an expert)"
            )
        if excluded_self:
            reason += f" ({excluded_self} self-judgment(s) excluded)"
        return unavailable(
            _LEVEL, reason, source=SignalSource.HUMAN, subject=subject, **evidence
        )

    min_judgments = int(ctx.params.get("min_judgments", 3))
    min_judges = int(ctx.params.get("min_judges", 1))
    win_rate = wins / n
    evidence["win_rate"] = win_rate

    if n < min_judgments or len(judges) < min_judges:
        return make_result(
            _LEVEL,
            CheckStatus.INCONCLUSIVE,
            (
                f"{n} judgment(s) from {len(judges)} judge(s) is below the "
                f"threshold ({min_judgments} judgments, {min_judges} judges) "
                "needed to decide"
            ),
            source=SignalSource.HUMAN,
            score=win_rate,
            subject=subject,
            subject_kind="workflow",
            evidence=evidence,
        )

    min_win_rate = float(ctx.params.get("min_win_rate", 0.5))
    if win_rate + 1e-12 < min_win_rate:
        return make_result(
            _LEVEL,
            CheckStatus.FAIL,
            (
                f"experts preferred this result in {wins}/{n} comparison(s) "
                f"(win rate {win_rate:.2f} < {min_win_rate})"
            ),
            source=SignalSource.HUMAN,
            score=win_rate,
            subject=subject,
            evidence=evidence,
        )

    return make_result(
        _LEVEL,
        CheckStatus.PASS,
        (
            f"experts preferred this result in {wins}/{n} comparison(s) "
            f"(win rate {win_rate:.2f})"
        ),
        source=SignalSource.HUMAN,
        score=win_rate,
        subject=subject,
        evidence=evidence,
    )


__all__ = ["expert_pairwise"]
