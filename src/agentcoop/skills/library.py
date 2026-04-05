from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agentcoop.runtime.skills import LoadedSkill, SkillRegistry

JsonDict = Dict[str, Any]


def _tokenize(text: str) -> List[str]:
    return text.lower().replace("_", " ").replace("-", " ").split()


class _BM25Index:
    """Minimal BM25 index over tokenized documents."""
    K1 = 1.5
    B = 0.75

    def __init__(self, corpus: List[List[str]]) -> None:
        self._corpus = corpus
        self._avg_dl = sum(len(doc) for doc in corpus) / max(len(corpus), 1)
        n = len(corpus)
        df: Counter = Counter()
        for doc in corpus:
            df.update(set(doc))
        self._idf = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

    def score(self, query_terms: List[str], doc_idx: int) -> float:
        doc = self._corpus[doc_idx]
        dl = len(doc)
        tf_map = Counter(doc)
        total = 0.0
        for term in query_terms:
            idf = self._idf.get(term, 0.0)
            if idf == 0.0:
                continue
            tf = tf_map.get(term, 0)
            numerator = tf * (self.K1 + 1)
            denominator = tf + self.K1 * (1 - self.B + self.B * dl / self._avg_dl)
            total += idf * numerator / denominator
        return total

    def search(self, query: str, top_k: int = 10) -> List[Tuple[int, float]]:
        query_terms = _tokenize(query)
        scored = [(i, self.score(query_terms, i)) for i in range(len(self._corpus))]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class SkillLibrary:
    """Unified index over agent-skills and meta-skills with BM25 search."""

    def __init__(
        self,
        search_paths: Optional[Sequence[Path]] = None,
        *,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self._search_paths = [Path(p) for p in (search_paths or [])]
        self._cache_dir = cache_dir
        self._agent_skills: List[LoadedSkill] = []
        self._meta_skills: List[LoadedSkill] = []
        self._agent_index: Optional[_BM25Index] = None
        self._meta_index: Optional[_BM25Index] = None
        self._scan()

    def _scan(self) -> None:
        registry = SkillRegistry(search_paths=[str(p) for p in self._search_paths])
        # Override search_paths to use only the explicitly provided paths,
        # bypassing the default paths added by _normalize_search_paths.
        registry.search_paths = [p.resolve() for p in self._search_paths]
        registry._ensure_index()

        seen_paths: set = set()
        for path_list in registry._paths_by_name.values():
            for path in path_list:
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                skill = registry._load_skill(path)
                if skill.skill_type == "meta-skill":
                    self._meta_skills.append(skill)
                else:
                    self._agent_skills.append(skill)

    @property
    def agent_skills(self) -> List[LoadedSkill]:
        return list(self._agent_skills)

    @property
    def meta_skills(self) -> List[LoadedSkill]:
        return list(self._meta_skills)

    def _ensure_agent_index(self) -> _BM25Index:
        if self._agent_index is None:
            corpus = [
                _tokenize(" ".join([
                    s.name, s.description,
                    *s.domain_tags, *s.capability_tags,
                    s.body[:200],
                ]))
                for s in self._agent_skills
            ]
            self._agent_index = _BM25Index(corpus)
        return self._agent_index

    def _ensure_meta_index(self) -> _BM25Index:
        if self._meta_index is None:
            corpus = [
                _tokenize(" ".join([
                    s.name, s.description,
                    *s.domain_tags, *s.capability_tags,
                    s.body[:200],
                ]))
                for s in self._meta_skills
            ]
            self._meta_index = _BM25Index(corpus)
        return self._meta_index

    def search_meta_skills(
        self, query: str, top_k: int = 5
    ) -> List[Tuple[LoadedSkill, float]]:
        index = self._ensure_meta_index()
        results = index.search(query, top_k=top_k)
        return [(self._meta_skills[i], score) for i, score in results if score > 0]

    def search_agent_skills(
        self, query: str, top_k: int = 10
    ) -> List[Tuple[LoadedSkill, float]]:
        index = self._ensure_agent_index()
        results = index.search(query, top_k=top_k)
        return [(self._agent_skills[i], score) for i, score in results if score > 0]
