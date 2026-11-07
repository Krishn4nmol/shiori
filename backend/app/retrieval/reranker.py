"""Cross-encoder reranker: reads the question and each candidate together and scores how well
the passage answers it. Much more accurate than vector similarity, but too slow to run over
the whole library, so it only re-scores the top candidates from hybrid search."""

from functools import lru_cache
from typing import Protocol

from app.config import get_settings


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance in [0, 1] for each passage, in the same order."""
        ...


class BGEReranker:
    def __init__(self, model_name: str, bf16: bool = True) -> None:
        import torch
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name, device="cpu", max_length=512)
        if bf16:
            self.model.model.to(torch.bfloat16)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        scores = self.model.predict([(query, p) for p in passages], batch_size=8)
        return [float(s) for s in scores]


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    return BGEReranker(settings.reranker_model, bf16=settings.reranker_bf16)
