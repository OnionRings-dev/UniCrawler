from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    redis_addr: str
    redis_password: str
    redis_db: int
    postgres_dsn: str
    mapper_input_queue: str
    mapper_output_queue: str
    parser_output_queue: str
    vectorizer_processing_queue: str
    vectorizer_failed_queue: str
    vectorizer_oversized_queue: str
    replay_batch_size: int


def env_string(key: str, fallback: str) -> str:
    return os.getenv(key) or fallback


def env_int(key: str, fallback: int) -> int:
    raw = os.getenv(key)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def load_config() -> Config:
    return Config(
        redis_addr=env_string("REDIS_ADDR", "redis:6379"),
        redis_password=env_string("REDIS_PASSWORD", ""),
        redis_db=env_int("REDIS_DB", 0),
        postgres_dsn=env_string(
            "POSTGRES_DSN",
            "postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable",
        ),
        mapper_input_queue=env_string("MAPPER_INPUT_QUEUE", "mapper:in"),
        mapper_output_queue=env_string("MAPPER_OUTPUT_QUEUE", "mapper:out"),
        parser_output_queue=env_string("PARSER_OUTPUT_QUEUE", "parser:out"),
        vectorizer_processing_queue=env_string(
            "VECTORIZER_PROCESSING_QUEUE", "vectorizer:processing"
        ),
        vectorizer_failed_queue=env_string("VECTORIZER_FAILED_QUEUE", "vectorizer:failed"),
        vectorizer_oversized_queue=env_string(
            "VECTORIZER_OVERSIZED_QUEUE", "vectorizer:oversized"
        ),
        replay_batch_size=env_int("REPLAY_BATCH_SIZE", 1000),
    )


def redis_connection_settings(config: Config) -> dict[str, Any]:
    if "://" in config.redis_addr:
        parsed = urlparse(config.redis_addr)
        return {
            "host": parsed.hostname or "redis",
            "port": parsed.port or 6379,
            "password": config.redis_password or parsed.password or None,
            "db": config.redis_db,
        }
    host, _, port = config.redis_addr.partition(":")
    return {
        "host": host or "redis",
        "port": int(port) if port else 6379,
        "password": config.redis_password or None,
        "db": config.redis_db,
    }
