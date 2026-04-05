from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from agentcoop.integrations.tool_registry import ToolCandidate
from agentcoop.ir.schema import NodeSpec, ToolDiscoverySpec

JsonDict = Dict[str, Any]

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SEARCH_TOKENS = {
    "browse",
    "citation",
    "citations",
    "evidence",
    "internet",
    "latest",
    "lookup",
    "news",
    "search",
    "source",
    "sources",
    "web",
}


@dataclass(frozen=True)
class RankedToolCandidate:
    candidate: ToolCandidate
    score: float
    reason: str


class ToolScout:
    """Deterministically narrows a registry-wide tool set to a small candidate pool."""

    def select_candidates(
        self,
        node: NodeSpec,
        inputs: Mapping[str, Any],
        candidates: Sequence[ToolCandidate],
        *,
        discovery: ToolDiscoverySpec,
        skill_text: str = "",
    ) -> tuple[List[ToolCandidate], JsonDict]:
        query_text = self._build_query_text(node, inputs, skill_text=skill_text)
        query_tokens = self._tokenize(query_text)
        search_intent = bool(_SEARCH_TOKENS & query_tokens)
        ranked: List[RankedToolCandidate] = []
        for candidate in candidates:
            descriptor_tokens = self._descriptor_tokens(candidate)
            overlap = query_tokens & descriptor_tokens
            score = float(len(overlap))
            reasons: List[str] = []
            if overlap:
                reasons.append(f"token overlap={len(overlap)}")
            if candidate.source == "bound":
                score += 0.25
                reasons.append("bound-tool prior")
            if search_intent and self._looks_like_web_tool(candidate):
                score += 3.0
                reasons.append("search-intent boost")
            if node.role.lower() in " ".join(descriptor_tokens):
                score += 0.5
                reasons.append("role token match")
            if score > 0:
                ranked.append(
                    RankedToolCandidate(
                        candidate=candidate,
                        score=score,
                        reason=", ".join(reasons) or "fallback score",
                    )
                )

        ranked.sort(
            key=lambda item: (
                item.score,
                item.candidate.source == "bound",
                item.candidate.ref.server,
                item.candidate.ref.tool,
            ),
            reverse=True,
        )

        if ranked:
            selected = [item.candidate for item in ranked[: discovery.max_candidate_tools]]
        else:
            selected = list(candidates[: discovery.max_candidate_tools])

        summary = {
            "query_text": query_text,
            "query_tokens": sorted(query_tokens),
            "search_intent": search_intent,
            "ranked_candidates": [
                {
                    "server": item.candidate.ref.server,
                    "tool": item.candidate.ref.tool,
                    "source": item.candidate.source,
                    "score": round(item.score, 3),
                    "reason": item.reason,
                }
                for item in ranked[: discovery.max_candidate_tools]
            ],
            "selected_candidates": [
                {
                    "server": candidate.ref.server,
                    "tool": candidate.ref.tool,
                    "source": candidate.source,
                }
                for candidate in selected
            ],
        }
        return selected, summary

    @staticmethod
    def _build_query_text(node: NodeSpec, inputs: Mapping[str, Any], *, skill_text: str = "") -> str:
        task = inputs.get("task", {})
        task_text = ""
        if isinstance(task, Mapping):
            task_text = " ".join(
                str(task.get(key, ""))
                for key in ("title", "description")
                if task.get(key)
            )
        input_keys = " ".join(str(key) for key in inputs.keys())
        value_text = ToolScout._stringify(inputs)
        parts = [
            node.role,
            node.description,
            node.system_prompt or "",
            skill_text,
            task_text,
            input_keys,
            value_text,
        ]
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _descriptor_tokens(candidate: ToolCandidate) -> set[str]:
        descriptor = candidate.descriptor
        parts = [
            candidate.ref.server,
            candidate.ref.tool,
            str(descriptor.get("name", "")),
            str(descriptor.get("description", "")),
        ]
        return ToolScout._tokenize(" ".join(parts))

    @staticmethod
    def _looks_like_web_tool(candidate: ToolCandidate) -> bool:
        haystack = " ".join(
            [
                candidate.ref.server,
                candidate.ref.tool,
                str(candidate.descriptor.get("name", "")),
                str(candidate.descriptor.get("description", "")),
            ]
        ).lower()
        return any(token in haystack for token in ("search", "browse", "web", "page", "fetch"))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in _TOKEN_RE.findall(text.lower()) if len(token) > 1}

    @staticmethod
    def _stringify(value: Any, *, max_chars: int = 1200) -> str:
        if isinstance(value, str):
            return value[:max_chars]
        try:
            serialized = json.dumps(value, ensure_ascii=True, sort_keys=True)
        except TypeError:
            serialized = repr(value)
        return serialized[:max_chars]
