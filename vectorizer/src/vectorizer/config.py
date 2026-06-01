from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    redis_addr: str
    redis_password: str
    redis_db: int
    input_queue: str
    processing_queue: str
    failed_queue: str
    oversized_queue: str
    queue_block_time: int
    redis_socket_timeout: float
    postgres_dsn: str
    qdrant_url: str
    qdrant_api_key: str | None
    qdrant_timeout: int
    collection_prefix: str
    collection_scope: str
    embedding_provider: str
    embedding_model: str
    openai_api_key: str | None
    batch_size: int
    chunk_tokens: int
    chunk_overlap_tokens: int
    min_chunk_tokens: int
    max_document_chars: int
    max_chunks_per_document: int
    summary_group_chunks: int
    max_summary_chunks: int
    summary_tokens: int
    max_retries: int
    retry_backoff_seconds: float


def load_config() -> Config:
    chunk_tokens = env_int("CHUNK_TOKENS", 700)
    overlap = env_int("CHUNK_OVERLAP_TOKENS", 100)
    if overlap >= chunk_tokens:
        overlap = max(0, chunk_tokens // 5)

    provider = env_string("EMBEDDING_PROVIDER", "fastembed").lower()
    default_model = "BAAI/bge-small-en-v1.5"
    if provider == "openai":
        default_model = "text-embedding-3-small"

    queue_block_time = env_int("QUEUE_BLOCK_TIME", 5)

    return Config(
        redis_addr=env_string("REDIS_ADDR", "redis:6379"),
        redis_password=env_string("REDIS_PASSWORD", ""),
        redis_db=env_int("REDIS_DB", 0),
        input_queue=env_string("INPUT_QUEUE", "parser:out"),
        processing_queue=env_string("PROCESSING_QUEUE", "vectorizer:processing"),
        failed_queue=env_string("FAILED_QUEUE", "vectorizer:failed"),
        oversized_queue=env_string("OVERSIZED_QUEUE", "vectorizer:oversized"),
        queue_block_time=queue_block_time,
        redis_socket_timeout=env_float("REDIS_SOCKET_TIMEOUT", queue_block_time + 10.0),
        postgres_dsn=env_string(
            "POSTGRES_DSN",
            "postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable",
        ),
        qdrant_url=env_string("QDRANT_URL", "http://qdrant:6333"),
        qdrant_api_key=env_optional("QDRANT_API_KEY"),
        qdrant_timeout=env_int("QDRANT_TIMEOUT", 30),
        collection_prefix=env_string("COLLECTION_PREFIX", "unicrawler"),
        collection_scope=env_string("COLLECTION_SCOPE", "domain").lower(),
        embedding_provider=provider,
        embedding_model=env_string("EMBEDDING_MODEL", default_model),
        openai_api_key=env_optional("OPENAI_API_KEY"),
        batch_size=env_int("BATCH_SIZE", 32),
        chunk_tokens=chunk_tokens,
        chunk_overlap_tokens=overlap,
        min_chunk_tokens=env_int("MIN_CHUNK_TOKENS", 40),
        max_document_chars=env_int("MAX_DOCUMENT_CHARS", 0),
        max_chunks_per_document=env_int("MAX_CHUNKS_PER_DOCUMENT", 1000),
        summary_group_chunks=env_int("SUMMARY_GROUP_CHUNKS", 40),
        max_summary_chunks=env_int("MAX_SUMMARY_CHUNKS", 500),
        summary_tokens=env_int("SUMMARY_TOKENS", 450),
        max_retries=env_int("MAX_RETRIES", 3),
        retry_backoff_seconds=env_float("RETRY_BACKOFF_SECONDS", 2.0),
    )


def env_string(key: str, fallback: str) -> str:
    return os.getenv(key) or fallback


def env_optional(key: str) -> str | None:
    value = os.getenv(key)
    return value if value else None


def env_int(key: str, fallback: int) -> int:
    try:
        return int(os.getenv(key, ""))
    except ValueError:
        return fallback


def env_float(key: str, fallback: float) -> float:
    try:
        return float(os.getenv(key, ""))
    except ValueError:
        return fallback
