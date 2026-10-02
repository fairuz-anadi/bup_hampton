"""High-level FuelGuard RAG orchestration pipeline.

Coordinates ingestion, semantic retrieval, re-ranking, and grounded source-attributed
question answering.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.contracts import ComponentHealth
from app.obs.logging import log_event
from app.rag.ingestion.chunker import TextChunker
from app.rag.ingestion.embeddings import EmbeddingGenerator
from app.rag.ingestion.ingest import IngestionEngine
from app.rag.retrieval.filters import MetadataFilter
from app.rag.retrieval.reranker import ResultReranker
from app.rag.retrieval.retriever import HybridRetriever, RetrievalResult
from app.rag.store import RAGStore

def find_default_data_dir() -> Path:
    env_dir = os.getenv("RAG_DATA_DIR")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)
    p = Path(__file__).resolve()
    for parent in [p.parent, p.parents[1], p.parents[2]]:
        candidate = parent / "rag_data"
        if candidate.is_dir():
            return candidate
    if len(p.parents) > 3:
        cand3 = p.parents[3] / "rag_data"
        if cand3.is_dir():
            return cand3
    return Path("/app/rag_data")


DEFAULT_DATA_DIR = find_default_data_dir()


class RAGPipeline:
    def __init__(
        self,
        dsn: str | None = None,
        data_dir: str | Path | None = None,
        api_key: str | None = None,
    ):
        self.data_dir = Path(data_dir or os.getenv("RAG_DATA_DIR", DEFAULT_DATA_DIR))
        self.store = RAGStore(dsn=dsn)
        self.embedder = EmbeddingGenerator(api_key=api_key)
        self.chunker = TextChunker()
        self.ingestion = IngestionEngine(store=self.store, chunker=self.chunker, embedder=self.embedder)
        self.retriever = HybridRetriever(store=self.store, embedder=self.embedder)
        self.reranker = ResultReranker()

    async def initialize(self) -> None:
        """Connect to DB and auto-ingest default directory if store is empty."""
        await self.store.connect()
        if len(self.store.get_all_chunks()) == 0 and self.data_dir.exists():
            log_event("rag.auto_ingest_start", data_dir=str(self.data_dir))
            self.ingest(self.data_dir, force=False)
            await self.store.persist_all()

    def ingest(self, data_dir: str | Path | None = None, force: bool = False) -> dict[str, Any]:
        target = Path(data_dir or self.data_dir)
        return self.ingestion.ingest_directory(target, force=force)

    def search(
        self,
        query: str,
        category: str | list[str] | None = None,
        filters: MetadataFilter | dict[str, Any] | None = None,
        top_k: int = 5,
        rerank: bool = True,
    ) -> list[RetrievalResult]:
        candidates = self.retriever.retrieve(
            query=query,
            category=category,
            filters=filters,
            top_k=max(top_k * 2, 10) if rerank else top_k,
        )
        if rerank:
            return self.reranker.rerank(query, candidates, top_k=top_k)
        return candidates[:top_k]

    def ask(
        self,
        query: str,
        category: str | list[str] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        results = self.search(query=query, category=category, top_k=top_k, rerank=True)
        if not results:
            return {
                "answer": "No relevant project documentation or operational rules found matching the query.",
                "sources": [],
                "results": [],
            }

        citations = []
        for r in results:
            citations.append({
                "source": r.source,
                "document_id": r.document_id,
                "section": r.section,
                "page": r.page,
                "category": r.category,
                "score": round(r.score, 4),
            })

        # Answer synthesis: LLM if API key configured, otherwise grounded extractive synthesis
        openai_key = self.embedder.api_key
        if openai_key:
            try:
                answer = self._synthesize_llm(query, results, openai_key)
                return {"answer": answer, "sources": citations, "results": [r.as_dict() for r in results], "mode": "llm"}
            except Exception as exc:
                log_event("rag.llm_synthesis_failed", error=str(exc)[:150])

        answer = self._synthesize_extractive(query, results)
        return {"answer": answer, "sources": citations, "results": [r.as_dict() for r in results], "mode": "extractive"}

    def _synthesize_llm(self, query: str, results: list[RetrievalResult], api_key: str) -> str:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        context_blocks = []
        for i, r in enumerate(results, start=1):
            sec = f" (Section: {r.section})" if r.section else ""
            context_blocks.append(f"[{i}] SOURCE: {r.source}{sec} (Category: {r.category}):\n{r.content}")

        context_str = "\n\n---\n\n".join(context_blocks)
        prompt = (
            f"You are the FuelGuard Knowledge Assistant. Answer the question based STRICTLY and ONLY on the provided context.\n"
            f"Always cite the source document filename (e.g. `fuel_allocation_rules.md`) when stating rules or facts.\n"
            f"Do NOT invent numbers, rules, or facts. If the context does not contain the answer, say so clearly.\n\n"
            f"CONTEXT:\n{context_str}\n\n"
            f"QUESTION: {query}"
        )

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1, api_key=api_key, timeout=10.0)
        resp = llm.invoke([
            SystemMessage(content="You are a precise technical assistant for the FuelGuard decision-support system."),
            HumanMessage(content=prompt),
        ])
        return str(resp.content).strip()

    def _synthesize_extractive(self, query: str, results: list[RetrievalResult]) -> str:
        """Deterministic, grounded extractive synthesis that never invents facts."""
        top = results[0]
        summary_lines = [
            f"Based on **{top.source}** ({top.category}):",
            f"> {top.content[:350].strip()}...",
        ]

        if len(results) > 1:
            other_sources = list(dict.fromkeys(r.source for r in results[1:3]))
            summary_lines.append(f"\nAdditional relevant context found in: {', '.join(f'`{s}`' for s in other_sources)}.")

        return "\n".join(summary_lines)

    def health(self) -> ComponentHealth:
        counts = self.store.count()
        if counts["chunks"] == 0:
            return ComponentHealth(name="RAG Knowledge Base", status="degraded", detail="Knowledge store empty")
        return ComponentHealth(
            name="RAG Knowledge Base",
            status="healthy",
            detail=f"{counts['documents']} documents, {counts['chunks']} chunks indexed",
        )


_PIPELINE_INSTANCE: RAGPipeline | None = None


def get_rag_pipeline(dsn: str | None = None) -> RAGPipeline:
    global _PIPELINE_INSTANCE
    if _PIPELINE_INSTANCE is None:
        _PIPELINE_INSTANCE = RAGPipeline(dsn=dsn)
    return _PIPELINE_INSTANCE
