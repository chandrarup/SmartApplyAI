"""Embedding adapter for semantic retrieval."""

from __future__ import annotations

import os
from typing import Iterable

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = os.getenv("SMARTAPPLY_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

_MODEL: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _MODEL


def embed(texts: Iterable[str]) -> list[list[float]]:
    """Embed texts using the configured local sentence-transformers model."""
    text_list = [str(t or "") for t in texts]
    if not text_list:
        return []
    model = _get_model()
    vectors = model.encode(
        text_list,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
