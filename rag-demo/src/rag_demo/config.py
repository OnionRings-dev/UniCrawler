from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parent


def _load_env() -> None:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / ".env", override=True)


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def _env_int(*names: str, default: int) -> int:
    value = _env(*names)
    return int(value) if value is not None else default


def _env_bool(*names: str, default: bool) -> bool:
    value = _env(*names)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _env_csv(*names: str) -> list[str]:
    value = _env(*names, default="")
    return [item.strip() for item in value.split(",") if item.strip()]


def _qdrant_url() -> str:
    explicit = _env("RAG_QDRANT_URL")
    if explicit:
        return explicit

    inherited = _env("QDRANT_URL")
    if not inherited:
        return "http://localhost:6333"

    parsed = urlparse(inherited)
    if parsed.hostname == "qdrant":
        port = f":{parsed.port}" if parsed.port else ""
        return urlunparse(parsed._replace(netloc=f"localhost{port}"))
    return inherited


@dataclass(frozen=True)
class Settings:
    qdrant_url: str
    qdrant_api_key: str | None
    qdrant_timeout: int
    collection_prefix: str
    collections: list[str]
    embedding_provider: str
    embedding_model: str
    openai_api_key: str | None
    groq_api_key: str | None
    chat_provider: str
    chat_model: str
    retrieval_limit: int
    candidate_limit: int
    context_limit: int
    source_limit: int
    reranker_provider: str
    reranker_model: str
    keyword_boost: bool
    max_tool_results: int


def load_settings() -> Settings:
    _load_env()
    embedding_provider = _env(
        "RAG_EMBEDDING_PROVIDER",
        "VECTORIZER_EMBEDDING_PROVIDER",
        "EMBEDDING_PROVIDER",
        default="fastembed",
    ).lower()
    default_embedding_model = (
        "text-embedding-3-small"
        if embedding_provider == "openai"
        else "BAAI/bge-small-en-v1.5"
    )

    return Settings(
        qdrant_url=_qdrant_url(),
        qdrant_api_key=_env("RAG_QDRANT_API_KEY", "QDRANT_API_KEY"),
        qdrant_timeout=_env_int("RAG_QDRANT_TIMEOUT", "QDRANT_TIMEOUT", default=30),
        collection_prefix=_env(
            "RAG_COLLECTION_PREFIX",
            "VECTORIZER_COLLECTION_PREFIX",
            "COLLECTION_PREFIX",
            default="unicrawler",
        ),
        collections=_env_csv("RAG_COLLECTIONS", "QDRANT_COLLECTIONS"),
        embedding_provider=embedding_provider,
        embedding_model=_env(
            "RAG_EMBEDDING_MODEL",
            "VECTORIZER_EMBEDDING_MODEL",
            "EMBEDDING_MODEL",
            default=default_embedding_model,
        ),
        openai_api_key=_env("OPENAI_API_KEY"),
        groq_api_key=_env("GROQ_API_KEY"),
        chat_provider=_env("RAG_CHAT_PROVIDER", default="groq").lower(),
        chat_model=_env("RAG_CHAT_MODEL", default="llama-3.3-70b-versatile"),
        retrieval_limit=_env_int("RAG_CONTEXT_LIMIT", default=5),
        candidate_limit=_env_int("RAG_CANDIDATE_LIMIT", default=30),
        context_limit=_env_int("RAG_CONTEXT_LIMIT", default=5),
        source_limit=_env_int("RAG_SOURCE_LIMIT", default=3),
        reranker_provider=_env("RAG_RERANKER_PROVIDER", default="local").lower(),
        reranker_model=_env(
            "RAG_RERANKER_MODEL",
            default="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        ),
        keyword_boost=_env_bool("RAG_KEYWORD_BOOST", default=True),
        max_tool_results=_env_int("RAG_MAX_TOOL_RESULTS", default=12),
    )
