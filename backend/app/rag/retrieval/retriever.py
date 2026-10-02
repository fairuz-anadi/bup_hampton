"""Semantic similarity retriever for FuelGuard RAG.

Performs hybrid retrieval combining dense vector cosine similarity and exact token
frequency overlap, filtered by metadata constraints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.rag.ingestion.chunker import DocumentChunk
from app.rag.ingestion.embeddings import EmbeddingGenerator, cosine_similarity
from app.rag.retrieval.filters import MetadataFilter, apply_filters
from app.rag.store import RAGStore


@dataclass
class RetrievalResult:
    content: str
    source: str
    page: int | None
    category: str
    score: float
    document_id: str
    section: str | None = None
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "source": self.source,
            "page": self.page,
            "category": self.category,
            "score": round(self.score, 4),
            "document_id": self.document_id,
            "section": self.section,
            "metadata": self.metadata or {},
        }


class HybridRetriever:
    def __init__(self, store: RAGStore, embedder: EmbeddingGenerator | None = None):
        self.store = store
        self.embedder = embedder or EmbeddingGenerator()

    def retrieve(
        self,
        query: str,
        category: str | list[str] | None = None,
        filters: MetadataFilter | dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        all_chunks = self.store.get_all_chunks()
        if not all_chunks:
            return []

        # Merge category into filter if specified
        combined_filter = filters or {}
        if isinstance(combined_filter, dict):
            if category:
                combined_filter["category"] = category
            filter_obj = MetadataFilter(**combined_filter)
        else:
            filter_obj = combined_filter
            if category:
                filter_obj.category = category

        filtered_chunks = apply_filters(all_chunks, filter_obj)
        if not filtered_chunks:
            return []

        # Generate query embedding
        query_emb = self.embedder.embed_query(query)
        query_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-\.%]+\b", query.lower()))

        scored_results: list[RetrievalResult] = []

        for chunk in filtered_chunks:
            # 1. Dense Cosine Similarity
            dense_score = 0.0
            if chunk.embedding and query_emb:
                dense_score = cosine_similarity(query_emb, chunk.embedding)

            # 2. Lexical / Keyword Overlap Boost
            lexical_score = 0.0
            if query_tokens:
                chunk_lower = chunk.content.lower()
                matched = sum(1 for token in query_tokens if token in chunk_lower)
                lexical_score = matched / len(query_tokens)

            # Combined hybrid score (70% semantic, 30% lexical keyword overlap)
            final_score = (0.70 * max(0.0, dense_score)) + (0.30 * lexical_score)

            scored_results.append(
                RetrievalResult(
                    content=chunk.content,
                    source=chunk.filename,
                    page=chunk.page,
                    category=chunk.category,
                    score=final_score,
                    document_id=chunk.document_id,
                    section=chunk.section,
                    metadata=chunk.metadata,
                )
            )

        scored_results.sort(key=lambda r: r.score, reverse=True)
        return scored_results[:top_k]
