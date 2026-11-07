"""Text -> vector. bge-m3 runs locally on CPU and works across English and Japanese."""

import hashlib
import math
from functools import lru_cache
from typing import Protocol

from app.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Unit-length vectors, so cosine similarity is a dot product."""
        ...


class BGEEmbedder:
    def __init__(self, model_name: str, dim: int, max_seq_length: int, batch_size: int) -> None:
        # Imported here: torch takes several seconds to load and the API process never needs it.
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device="cpu")
        self.model.max_seq_length = max_seq_length
        self.batch_size = batch_size
        self.dim = dim
        actual = self.model.get_embedding_dimension()
        if actual != dim:
            raise ValueError(f"{model_name} outputs {actual} dims but EMBEDDING_DIM is {dim}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        return vectors.tolist()


class HashEmbedder:
    """Deterministic fake embeddings for tests: fast and needs no model download."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            raw = [digest[i % len(digest)] - 127.5 for i in range(self.dim)]
            norm = math.sqrt(sum(x * x for x in raw))
            out.append([x / norm for x in raw])
        return out


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    return BGEEmbedder(
        s.embedding_model, s.embedding_dim, s.embedding_max_seq_length, s.embedding_batch_size
    )
