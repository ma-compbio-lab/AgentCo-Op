from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from dynaforge.ir.schema import FailureType, WorkflowBlueprint
from dynaforge.runtime.reports import BlameCandidate, ExecutionReport, HardCheckResult, NodeTrace


class FailureClassifier:
    """Maps stderr/error text into stable failure categories."""

    _patterns: List[Tuple[re.Pattern[str], FailureType]] = [
        (re.compile(r"no module named|module not found|command not found", re.IGNORECASE), FailureType.env_missing_dep),
        (re.compile(r"version conflict|incompatible|cannot import name", re.IGNORECASE), FailureType.env_version_conflict),
        (re.compile(r"schema|validation|unexpected keyword|missing required", re.IGNORECASE), FailureType.tool_schema_mismatch),
        (re.compile(r"file not found|no such file|does not exist", re.IGNORECASE), FailureType.file_missing),
        (re.compile(r"timeout|timed out", re.IGNORECASE), FailureType.timeout),
        (re.compile(r"budget", re.IGNORECASE), FailureType.budget_exceeded),
        (re.compile(r"assertion|contract|invariant", re.IGNORECASE), FailureType.output_contract_violation),
    ]

    def classify_text(self, text: str) -> FailureType:
        for pattern, failure_type in self._patterns:
            if pattern.search(text):
                return failure_type
        return FailureType.unknown

    def classify_hard_check(self, result: HardCheckResult) -> FailureType:
        if result.failure_type != FailureType.none:
            return result.failure_type
        combined = "\n".join(part for part in [result.stderr, result.stdout] if part)
        return self.classify_text(combined)


class BlameAssigner:
    """Translates runtime signals into node/edge/tool/sandbox blame candidates."""

    def __init__(self, classifier: Optional[FailureClassifier] = None):
        self.classifier = classifier or FailureClassifier()

    def assign(self, blueprint: WorkflowBlueprint, report: ExecutionReport) -> List[BlameCandidate]:
        ranked: Dict[Tuple[str, str], BlameCandidate] = {}
        node_map = blueprint.node_map()

        def add(candidate: BlameCandidate) -> None:
            key = (candidate.target_type, candidate.target_id)
            existing = ranked.get(key)
            if existing is None or candidate.score > existing.score:
                ranked[key] = candidate

        for violation in report.contract_violations:
            add(
                BlameCandidate(
                    target_type="node",
                    target_id=violation.node_id,
                    score=0.95,
                    reason=f"Contract violation: {violation.message}",
                )
            )

        failed_traces = [trace for trace in report.traces if trace.status == "failed"]
        for trace in failed_traces:
            add(
                BlameCandidate(
                    target_type="node",
                    target_id=trace.node_id,
                    score=0.9,
                    reason=trace.error or f"Node failed with {trace.failure_type.value}",
                )
            )
            node = node_map.get(trace.node_id)
            if node:
                if node.tools:
                    tool = node.tools[0]
                    add(
                        BlameCandidate(
                            target_type="tool",
                            target_id=f"{tool.server}:{tool.tool}",
                            score=0.82,
                            reason=f"Primary tool bound to failed node {trace.node_id}",
                        )
                    )
                if node.sandbox:
                    add(
                        BlameCandidate(
                            target_type="sandbox",
                            target_id=node.sandbox.sandbox_id,
                            score=0.78,
                            reason=f"Sandbox attached to failed node {trace.node_id}",
                        )
                    )

        for check in report.hard_checks:
            if not check.passed:
                failure_type = self.classifier.classify_hard_check(check)
                add(
                    BlameCandidate(
                        target_type="eval",
                        target_id=check.check_id,
                        score=0.7,
                        reason=f"Hard check failed with {failure_type.value}",
                    )
                )

        if report.failure_type in (FailureType.env_missing_dep, FailureType.env_version_conflict):
            suspect = self._last_relevant_trace(report.traces)
            if suspect is not None:
                node = node_map.get(suspect.node_id)
                if node and node.sandbox:
                    add(
                        BlameCandidate(
                            target_type="sandbox",
                            target_id=node.sandbox.sandbox_id,
                            score=0.92,
                            reason=f"Failure type {report.failure_type.value} strongly suggests sandbox issue",
                        )
                    )

        if not ranked and report.traces:
            suspect = self._last_relevant_trace(report.traces)
            if suspect is not None:
                add(
                    BlameCandidate(
                        target_type="node",
                        target_id=suspect.node_id,
                        score=0.6,
                        reason="Fallback blame on last executed node",
                    )
                )

        return sorted(ranked.values(), key=lambda item: item.score, reverse=True)

    @staticmethod
    def _last_relevant_trace(traces: Iterable[NodeTrace]) -> Optional[NodeTrace]:
        ordered = list(traces)
        for trace in reversed(ordered):
            if trace.status in {"failed", "success", "cached"}:
                return trace
        return None
