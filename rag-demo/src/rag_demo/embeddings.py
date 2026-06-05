from __future__ import annotations

from abc import ABC, abstractmethod

from fastembed import TextEmbedding
from openai import OpenAI


class Embedder(ABC):
    model: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class FastEmbedder(Embedder):
    def __init__(self, model: str) -> None:
        self.model = model
        self._embedder = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._embedder.embed(texts)
        return [vector.tolist() for vector in vectors]


class OpenAIEmbedder(Embedder):
    def __init__(self, model: str, api_key: str | None) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when RAG_EMBEDDING_PROVIDER=openai")
        self.model = model
        self._client = OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


def make_embedder(provider: str, model: str, openai_api_key: str | None) -> Embedder:
    if provider == "fastembed":
        return FastEmbedder(model)
    if provider == "openai":
        return OpenAIEmbedder(model, openai_api_key)
    raise ValueError(f"Unsupported embedding provider: {provider}")
