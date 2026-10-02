"""FastAPI endpoints for FuelGuard RAG knowledge ingestion and search."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.rag.pipeline import get_rag_pipeline

router = APIRouter(prefix="/api/rag", tags=["RAG"])


class IngestRequest(BaseModel):
    data_dir: str | None = Field(None, description="Optional path to directory to ingest")
    force: bool = Field(False, description="Force re-embedding of unchanged documents")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000, description="Natural language search query")
    category: str | list[str] | None = Field(None, description="Category filter (e.g. rules_policies, project_documents)")
    top_k: int = Field(5, ge=1, le=50, description="Number of results to return")
    filters: dict[str, Any] | None = Field(None, description="Additional metadata filters")


class AskRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000, description="Question to answer from knowledge base")
    category: str | list[str] | None = Field(None, description="Category filter")
    top_k: int = Field(5, ge=1, le=20, description="Number of source chunks to cite")


def _pipeline(request: Request):
    svc = getattr(request.app.state, "services", None)
    dsn = svc.settings.database_url if svc and svc.settings.database_url else None
    return get_rag_pipeline(dsn=dsn)


@router.post("/ingest", summary="Ingest or update documents in the RAG knowledge base")
async def ingest_documents(body: IngestRequest, request: Request) -> dict[str, Any]:
    pipeline = _pipeline(request)
    try:
        res = pipeline.ingest(data_dir=body.data_dir, force=body.force)
        await pipeline.store.persist_all()
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INGESTION_FAILED", "message": str(exc)},
        )


@router.post("/search", summary="Search RAG knowledge base with semantic similarity and metadata filtering")
async def search_knowledge(body: SearchRequest, request: Request) -> dict[str, Any]:
    pipeline = _pipeline(request)
    results = pipeline.search(
        query=body.query,
        category=body.category,
        filters=body.filters,
        top_k=body.top_k,
        rerank=True,
    )
    return {"results": [r.as_dict() for r in results]}


@router.post("/ask", summary="Ask questions grounded strictly on retrieved RAG context and policy citations")
async def ask_question(body: AskRequest, request: Request) -> dict[str, Any]:
    pipeline = _pipeline(request)
    return pipeline.ask(
        query=body.query,
        category=body.category,
        top_k=body.top_k,
    )


@router.get("/stats", summary="Get RAG knowledge base statistics and document counts")
def get_rag_stats(request: Request) -> dict[str, Any]:
    pipeline = _pipeline(request)
    health = pipeline.health()
    counts = pipeline.store.count()
    return {
        "status": health.status,
        "detail": health.detail,
        "counts": counts,
        "data_dir": str(pipeline.data_dir),
        "offline_mode": pipeline.embedder.is_offline,
    }
