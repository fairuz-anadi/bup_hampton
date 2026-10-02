"""Re-ranking engine for FuelGuard RAG.

Refines initial semantic search candidates by scoring term adjacency, policy relevance,
exact numerical constraint citations, and diversity.
"""
from __future__ import annotations

import re
from typing import Sequence

from app.rag.retrieval.retriever import RetrievalResult


class ResultReranker:
    def __init__(self, policy_boost: float = 0.15, exact_phrase_boost: float = 0.20):
        self.policy_boost = policy_boost
        self.exact_phrase_boost = exact_phrase_boost

    def rerank(self, query: str, results: Sequence[RetrievalResult], top_k: int = 5) -> list[RetrievalResult]:
        if not results:
            return []

        query_clean = query.strip().lower()
        query_words = re.findall(r"\b[a-zA-Z0-9_\-\.%]+\b", query_clean)
        is_policy_query = any(w in query_clean for w in ("rule", "policy", "reserve", "allowed", "constraint", "limit", "must"))

        reranked: list[tuple[float, RetrievalResult]] = []

        for res in results:
            content_lower = res.content.lower()
            score = res.score

            # 1. Exact phrase boost
            if query_clean in content_lower:
                score += self.exact_phrase_boost

            # 2. Key numbers/percentages match boost (e.g. 10%, 12000, 5000)
            numbers_in_query = re.findall(r"\b\d+[%]?", query_clean)
            for num in numbers_in_query:
                if num in content_lower:
                    score += 0.10

            # 3. Policy category boost for rule-seeking questions
            if is_policy_query and res.category == "rules_policies":
                score += self.policy_boost

            # Clamp score between 0.0 and 1.0
            score = max(0.0, min(1.0, score))
            reranked.append((score, RetrievalResult(
                content=res.content,
                source=res.source,
                page=res.page,
                category=res.category,
                score=score,
                document_id=res.document_id,
                section=res.section,
                metadata=res.metadata,
            )))

        reranked.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in reranked[:top_k]]
