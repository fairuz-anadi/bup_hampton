"""Dense embeddings provider for FuelGuard RAG.

Provides OpenAI embedding generation when OPENAI_API_KEY is available, and an
in-process deterministic hash/dense embedding fallback for offline runs and testing.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Sequence


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Computes cosine similarity between two dense vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (math.sqrt(norm_a) * math.sqrt(norm_b))))


class EmbeddingGenerator:
    """Generates dense vector embeddings for documents and queries."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1536,
        api_key: str | None = None,
    ):
        self.model_name = model_name
        self.dimension = dimension
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.is_offline = not bool(self.api_key)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if not self.is_offline:
            try:
                return self._embed_openai(texts)
            except Exception:
                # Seamless fallback to in-process deterministic dense embeddings
                pass

        return [self._embed_deterministic(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        results = self.embed_texts([query])
        return results[0] if results else [0.0] * self.dimension

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Generates embeddings using OpenAI API client."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            resp = client.embeddings.create(input=texts, model=self.model_name)
            return [data.embedding for data in resp.data]
        except ImportError:
            import httpx
            with httpx.Client(timeout=10.0) as client:
                res = client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"input": texts, "model": self.model_name},
                )
                if res.status_code == 200:
                    data = res.json()
                    return [item["embedding"] for item in data["data"]]
                raise RuntimeError(f"OpenAI embedding error {res.status_code}: {res.text}")

    def _embed_deterministic(self, text: str) -> list[float]:
        """In-process deterministic feature hashing vector generator.

        Produces consistent, normalized dense representations capturing n-grams,
        keywords, and semantic frequency for exact and near-match similarity.
        """
        vec = [0.0] * self.dimension
        clean = text.lower()

        # Token extraction: unigrams and bigrams
        words = re.findall(r"\b[a-z0-9_\-\.]+\b", clean)
        tokens = list(words)
        for i in range(len(words) - 1):
            tokens.append(f"{words[i]}_{words[i+1]}")

        if not tokens:
            return vec

        for token in tokens:
            # MD5 hash into dimension index
            h_val = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16)
            idx = h_val % self.dimension
            sign = 1.0 if (h_val % 2 == 0) else -1.0
            # Term weight (slight boost for longer domain identifiers)
            weight = 1.0 + (0.1 * min(len(token), 10))
            vec[idx] += sign * weight

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec
