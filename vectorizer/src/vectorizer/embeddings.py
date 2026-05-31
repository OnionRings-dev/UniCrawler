from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from fastembed import TextEmbedding
from openai import OpenAI


class Embedder(ABC):
    model: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def dimension(self) -> int:
        return len(self.embed(["dimension probe"])[0])


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
            raise ValueError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
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
    raise ValueError(f"unsupported EMBEDDING_PROVIDER: {provider}")


def batched(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]

