"""Ingestion coordinator for FuelGuard RAG.

Scans data directories, loads multi-format documents, generates semantic chunks,
computes dense embeddings, and persists them into the RAG vector store.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.obs.logging import log_event
from app.rag.ingestion.chunker import DocumentChunk, TextChunker
from app.rag.ingestion.embeddings import EmbeddingGenerator
from app.rag.ingestion.loaders import DocumentLoader, LoadedDocument
from app.rag.store import RAGStore


class IngestionEngine:
    def __init__(
        self,
        store: RAGStore,
        chunker: TextChunker | None = None,
        embedder: EmbeddingGenerator | None = None,
    ):
        self.store = store
        self.chunker = chunker or TextChunker()
        self.embedder = embedder or EmbeddingGenerator()

    def ingest_directory(
        self,
        data_dir: str | Path,
        force: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        target = Path(data_dir)
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"Target directory does not exist: {target}")

        files_found = []
        for ext in DocumentLoader.SUPPORTED_EXTENSIONS:
            files_found.extend(target.rglob(f"*{ext}"))

        docs_loaded: list[LoadedDocument] = []
        all_chunks: list[DocumentChunk] = []

        for fpath in sorted(files_found):
            # Derive default category from parent folder name if in subfolder
            cat = fpath.parent.name if fpath.parent != target else "general"
            loaded = DocumentLoader.load(fpath, default_category=cat)

            for doc in loaded:
                existing = self.store.get_document(doc.document_id)
                if existing and existing.checksum == doc.checksum and not force:
                    # Skip unchanged document to avoid unnecessary embedding calculation
                    continue

                docs_loaded.append(doc)
                chunks = self.chunker.chunk_document(doc)
                all_chunks.extend(chunks)

        if all_chunks:
            # Batch generate embeddings for all new/modified chunks
            chunk_texts = [c.content for c in all_chunks]
            embeddings = self.embedder.embed_texts(chunk_texts)
            for chunk, emb in zip(all_chunks, embeddings):
                chunk.embedding = emb

            # Save in store
            self.store.save_documents_and_chunks(docs_loaded, all_chunks)

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log_event(
            "rag.ingestion_completed",
            files_scanned=len(files_found),
            docs_loaded=len(docs_loaded),
            chunks_created=len(all_chunks),
            duration_ms=duration_ms,
        )

        return {
            "status": "success",
            "files_scanned": len(files_found),
            "documents_indexed": len(docs_loaded),
            "chunks_created": len(all_chunks),
            "total_chunks_in_store": len(self.store.get_all_chunks()),
            "duration_ms": duration_ms,
        }

    def ingest_single_file(self, file_path: str | Path, category: str | None = None) -> dict[str, Any]:
        path = Path(file_path)
        loaded = DocumentLoader.load(path, default_category=category)
        if not loaded:
            return {"status": "skipped", "message": f"Unsupported or empty file: {path.name}"}

        chunks: list[DocumentChunk] = []
        for doc in loaded:
            chunks.extend(self.chunker.chunk_document(doc))

        if chunks:
            chunk_texts = [c.content for c in chunks]
            embeddings = self.embedder.embed_texts(chunk_texts)
            for chunk, emb in zip(chunks, embeddings):
                chunk.embedding = emb
            self.store.save_documents_and_chunks(loaded, chunks)

        return {
            "status": "success",
            "filename": path.name,
            "documents": len(loaded),
            "chunks": len(chunks),
        }
