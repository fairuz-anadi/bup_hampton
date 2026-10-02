"""Metadata filtering engine for FuelGuard RAG retrieval."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.ingestion.chunker import DocumentChunk


@dataclass
class MetadataFilter:
    category: str | list[str] | None = None
    document_id: str | list[str] | None = None
    document_type: str | list[str] | None = None
    section: str | None = None
    year: int | None = None
    filename: str | None = None

    def matches(self, chunk: DocumentChunk) -> bool:
        if self.category:
            cats = [self.category] if isinstance(self.category, str) else self.category
            if chunk.category not in cats:
                return False

        if self.document_id:
            dids = [self.document_id] if isinstance(self.document_id, str) else self.document_id
            if chunk.document_id not in dids:
                return False

        if self.document_type:
            dtypes = [self.document_type] if isinstance(self.document_type, str) else self.document_type
            if chunk.document_type not in dtypes:
                return False

        if self.section and chunk.section:
            if self.section.lower() not in chunk.section.lower():
                return False

        if self.year is not None and chunk.year is not None:
            if chunk.year != self.year:
                return False

        if self.filename and chunk.filename:
            if self.filename.lower() not in chunk.filename.lower():
                return False

        return True


def apply_filters(chunks: list[DocumentChunk], filters: MetadataFilter | dict[str, Any] | None) -> list[DocumentChunk]:
    if not filters:
        return chunks
    if isinstance(filters, dict):
        f = MetadataFilter(**filters)
    else:
        f = filters

    return [c for c in chunks if f.matches(c)]
