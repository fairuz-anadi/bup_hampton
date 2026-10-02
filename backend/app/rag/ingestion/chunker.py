"""Text chunker for FuelGuard RAG ingestion pipeline.

Implements semantic, header-aware text chunking with configurable token targets
(default 500-800 tokens) and overlap (50-100 tokens), preserving section hierarchies
and document metadata for every chunk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.ingestion.loaders import LoadedDocument


@dataclass
class DocumentChunk:
    chunk_id: str
    document_id: str
    filename: str
    category: str
    document_type: str
    version: str
    year: int | None
    section: str | None
    page: int | None
    chunk_index: int
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


def estimate_tokens(text: str) -> int:
    """Fast, accurate token estimation (~4 characters per token for English)."""
    # Simple whitespace split plus punctuation weight
    words = re.findall(r"\w+|[^\w\s]", text)
    return max(1, int(len(words) * 1.1))


class TextChunker:
    def __init__(
        self,
        min_chunk_tokens: int = 250,
        target_chunk_tokens: int = 650,
        max_chunk_tokens: int = 800,
        overlap_tokens: int = 75,
    ):
        self.min_chunk_tokens = min_chunk_tokens
        self.target_chunk_tokens = target_chunk_tokens
        self.max_chunk_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens

    def chunk_document(self, doc: LoadedDocument) -> list[DocumentChunk]:
        raw_text = doc.text.strip()
        if not raw_text:
            return []

        # Split into semantic blocks by markdown headings (#, ##, ###) or paragraph breaks
        blocks = self._split_into_blocks(raw_text)
        chunks: list[DocumentChunk] = []

        current_content: list[str] = []
        current_tokens = 0
        current_section = doc.section or "main"
        chunk_idx = 0

        for block_header, block_text in blocks:
            block_tokens = estimate_tokens(block_text)

            # If adding this block exceeds max_chunk_tokens and we already have sufficient content
            if current_tokens + block_tokens > self.max_chunk_tokens and current_tokens >= self.min_chunk_tokens:
                combined_text = "\n\n".join(current_content).strip()
                chunks.append(
                    self._create_chunk(
                        doc=doc,
                        chunk_idx=chunk_idx,
                        section=current_section,
                        content=combined_text,
                        tokens=current_tokens,
                    )
                )
                chunk_idx += 1

                # Retain overlap from end of previous block
                overlap_text = self._extract_overlap(current_content)
                current_content = [overlap_text] if overlap_text else []
                current_tokens = estimate_tokens(overlap_text) if overlap_text else 0

            current_content.append(block_text)
            current_tokens += block_tokens
            if block_header:
                current_section = block_header

        # Flush remaining content
        if current_content:
            combined_text = "\n\n".join(current_content).strip()
            if combined_text:
                chunks.append(
                    self._create_chunk(
                        doc=doc,
                        chunk_idx=chunk_idx,
                        section=current_section,
                        content=combined_text,
                        tokens=current_tokens,
                    )
                )

        return chunks

    def _split_into_blocks(self, text: str) -> list[tuple[str | None, str]]:
        """Splits markdown/text into section-tagged blocks."""
        lines = text.splitlines()
        blocks: list[tuple[str | None, str]] = []
        current_heading: str | None = None
        current_lines: list[str] = []

        for line in lines:
            if re.match(r"^#{1,4}\s+", line):
                if current_lines:
                    block_content = "\n".join(current_lines).strip()
                    if block_content:
                        blocks.append((current_heading, block_content))
                    current_lines = []
                current_heading = line.lstrip("#").strip()
                current_lines.append(line)
            else:
                current_lines.append(line)

        if current_lines:
            block_content = "\n".join(current_lines).strip()
            if block_content:
                blocks.append((current_heading, block_content))

        return blocks

    def _extract_overlap(self, content_blocks: list[str]) -> str:
        """Extracts approximately overlap_tokens from the tail of content_blocks."""
        joined = "\n\n".join(content_blocks)
        sentences = re.split(r"(?<=[.!?])\s+", joined)
        overlap_sentences: list[str] = []
        total_tokens = 0
        for sent in reversed(sentences):
            sent_tokens = estimate_tokens(sent)
            if total_tokens + sent_tokens > self.overlap_tokens:
                break
            overlap_sentences.append(sent)
            total_tokens += sent_tokens

        return " ".join(reversed(overlap_sentences)).strip()

    def _create_chunk(
        self, doc: LoadedDocument, chunk_idx: int, section: str | None, content: str, tokens: int
    ) -> DocumentChunk:
        chunk_id = f"{doc.document_id}-chk{chunk_idx:03d}"
        return DocumentChunk(
            chunk_id=chunk_id,
            document_id=doc.document_id,
            filename=doc.filename,
            category=doc.category,
            document_type=doc.document_type,
            version=doc.version,
            year=doc.year,
            section=section,
            page=doc.page,
            chunk_index=chunk_idx,
            content=content,
            token_count=tokens,
            metadata={
                **doc.metadata,
                "document_id": doc.document_id,
                "filename": doc.filename,
                "category": doc.category,
                "section": section,
                "page": doc.page,
                "chunk_index": chunk_idx,
            },
        )
