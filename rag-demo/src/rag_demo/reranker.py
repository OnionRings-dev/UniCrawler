from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from rag_demo.config import Settings
from rag_demo.retriever import RetrievalHit


class Reranker(Protocol):
    provider: str
    model: str

    def score(self, question: str, hits: list[RetrievalHit]) -> list[float]:
        raise NotImplementedError


@dataclass
class OffReranker:
    provider: str = "off"
    model: str = "off"

    def score(self, question: str, hits: list[RetrievalHit]) -> list[float]:
        return [0.0 for _ in hits]


class LocalCrossEncoderReranker:
    provider = "local"

    def __init__(self, model: str) -> None:
        self.model = model
        self._cross_encoder = None

    @property
    def cross_encoder(self):
        if self._cross_encoder is None:
            from sentence_transformers import CrossEncoder

            self._cross_encoder = CrossEncoder(self.model)
        return self._cross_encoder

    def score(self, question: str, hits: list[RetrievalHit]) -> list[float]:
        if not hits:
            return []
        pairs = [(question, hit_text(hit)) for hit in hits]
        scores = self.cross_encoder.predict(pairs)
        return [float(score) for score in scores]


def make_reranker(settings: Settings) -> Reranker:
    return make_cached_reranker(settings.reranker_provider, settings.reranker_model)


@lru_cache(maxsize=4)
def make_cached_reranker(provider: str, model: str) -> Reranker:
    if provider == "off":
        return OffReranker()
    if provider == "local":
        return LocalCrossEncoderReranker(model)
    raise ValueError(f"Unsupported RAG_RERANKER_PROVIDER: {provider}")


def hit_text(hit: RetrievalHit) -> str:
    payload = hit.payload
    title = payload.get("title") or ""
    headings = " > ".join(payload.get("headings") or [])
    text = payload.get("text") or ""
    return "\n".join(part for part in (str(title), headings, str(text)) if part)
