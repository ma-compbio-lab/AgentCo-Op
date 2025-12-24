from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.contracts import JudgeReport


@dataclass
class MethodResult:
    answer: str
    judge_report: JudgeReport | None = None
    state: dict[str, Any] | None = None
