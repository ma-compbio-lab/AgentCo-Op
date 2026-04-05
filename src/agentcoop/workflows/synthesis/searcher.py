from __future__ import annotations

import math
from collections import Counter
from typing import List, Tuple

from agentcoop.workflows.design import TaskProfile
from agentcoop.workflows.synthesis.component_library import ComponentLibrary, ComponentSpec


def _tokenize(text: str) -> List[str]:
    return text.lower().replace("_", " ").split()


class ComponentSearcher:
    """Ranks ComponentLibrary entries against a TaskProfile using BM25."""
    K1 = 1.5
    B = 0.75

    def __init__(self, library: ComponentLibrary, top_k: int = 10) -> None:
        self._components = library.components
        self._top_k = top_k
        self._corpus: List[List[str]] = [
            _tokenize(" ".join([c.role, c.description] + c.capability_tags + c.domain_tags))
            for c in self._components
        ]
        self._avg_dl = sum(len(doc) for doc in self._corpus) / max(len(self._corpus), 1)
        self._idf: dict[str, float] = self._compute_idf()

    def _compute_idf(self) -> dict[str, float]:
        n = len(self._corpus)
        df: Counter = Counter()
        for doc in self._corpus:
            df.update(set(doc))
        return {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

    def _bm25_score(self, query_terms: List[str], doc_idx: int) -> float:
        doc = self._corpus[doc_idx]
        dl = len(doc)
        tf_map = Counter(doc)
        score = 0.0
        for term in query_terms:
            if term not in self._idf:
                continue
            tf = tf_map.get(term, 0)
            numerator = tf * (self.K1 + 1)
            denominator = tf + self.K1 * (1 - self.B + self.B * dl / self._avg_dl)
            score += self._idf[term] * numerator / denominator
        return score

    def search(self, profile: TaskProfile) -> List[Tuple[ComponentSpec, float]]:
        """Return top-k (component, score) pairs ranked by BM25 against the profile."""
        query = _tokenize(
            " ".join([
                profile.domain,
                profile.answer_mode,
                profile.complexity,
                "tool" if profile.requires_tool_execution else "",
                "test" if profile.requires_test_execution else "",
                "repo" if profile.requires_repo_search else "",
                "closed_loop" if profile.requires_closed_loop else "",
                "specialist" if profile.requires_specialists else "",
                "code" if profile.answer_mode == "code" else "",
            ])
        )
        scored = [
            (self._components[i], self._bm25_score(query, i))
            for i in range(len(self._components))
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: self._top_k]
