from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from tenacity import retry, stop_after_attempt, wait_exponential

from vectorizer.chunking import MarkdownChunker
from vectorizer.config import Config, load_config
from vectorizer.embeddings import make_embedder
from vectorizer.logging import configure_logging
from vectorizer.messages import parse_message
from vectorizer.qdrant import QdrantSink
from vectorizer.store import PostgresStore

logger = logging.getLogger("vectorizer")


@dataclass
class Runtime:
    cfg: Config
    redis: Redis
    store: PostgresStore
    chunker: MarkdownChunker
    sink: QdrantSink
    stopping: bool = False


def main() -> None:
    configure_logging()
    cfg = load_config()
    redis = Redis(
        host=cfg.redis_addr.split(":", 1)[0],
        port=int(cfg.redis_addr.split(":", 1)[1]) if ":" in cfg.redis_addr else 6379,
        password=cfg.redis_password or None,
        db=cfg.redis_db,
        decode_responses=True,
        health_check_interval=30,
        retry_on_timeout=True,
        socket_connect_timeout=5,
        socket_timeout=cfg.redis_socket_timeout,
    )
    redis.ping()

    embedder = make_embedder(cfg.embedding_provider, cfg.embedding_model, cfg.openai_api_key)
    chunker = MarkdownChunker(
        chunk_tokens=cfg.chunk_tokens,
        overlap_tokens=cfg.chunk_overlap_tokens,
        min_chunk_tokens=cfg.min_chunk_tokens,
    )
    sink = QdrantSink(
        url=cfg.qdrant_url,
        api_key=cfg.qdrant_api_key,
        timeout=cfg.qdrant_timeout,
        collection_prefix=cfg.collection_prefix,
        collection_scope=cfg.collection_scope,
        embedder=embedder,
        batch_size=cfg.batch_size,
    )

    with PostgresStore(cfg.postgres_dsn) as store:
        runtime = Runtime(cfg, redis, store, chunker, sink)
        install_signal_handlers(runtime)
        logger.info(
            "vectorizer ready",
            extra={
                "_input_queue": cfg.input_queue,
                "_processing_queue": cfg.processing_queue,
                "_qdrant_url": cfg.qdrant_url,
                "_embedding_provider": cfg.embedding_provider,
                "_embedding_model": cfg.embedding_model,
                "_collection_scope": cfg.collection_scope,
            },
        )
        recover_processing_queue(runtime)
        run(runtime)


def run(runtime: Runtime) -> None:
    while not runtime.stopping:
        try:
            raw = runtime.redis.brpoplpush(
                runtime.cfg.input_queue,
                runtime.cfg.processing_queue,
                timeout=runtime.cfg.queue_block_time,
            )
        except RedisTimeoutError:
            logger.debug("redis poll timed out")
            continue
        except RedisConnectionError:
            logger.warning("redis connection lost during poll", exc_info=True)
            time.sleep(1)
            continue
        if raw is None:
            continue
        try:
            process_processing_item(runtime, raw)
        except RedisError:
            logger.warning("redis error while acknowledging message", exc_info=True)
            time.sleep(1)


def recover_processing_queue(runtime: Runtime) -> None:
    pending = runtime.redis.lrange(runtime.cfg.processing_queue, 0, -1)
    if not pending:
        return
    logger.info(
        "recovering processing queue",
        extra={"_processing_queue": runtime.cfg.processing_queue, "_pending": len(pending)},
    )
    for raw in pending:
        if runtime.stopping:
            break
        try:
            process_processing_item(runtime, raw)
        except RedisError:
            logger.warning("redis error while recovering message", exc_info=True)
            break


def process_processing_item(runtime: Runtime, raw: str | bytes) -> None:
    raw_value = raw.decode() if isinstance(raw, bytes) else raw
    try:
        process_message_with_retry(runtime, raw_value)
    except RedisError:
        raise
    except Exception:
        logger.exception("message failed", extra={"_raw": raw_value})
        runtime.redis.lrem(runtime.cfg.processing_queue, 1, raw_value)
        runtime.redis.lpush(runtime.cfg.failed_queue, raw_value)
    else:
        runtime.redis.lrem(runtime.cfg.processing_queue, 1, raw_value)


def process_message_with_retry(runtime: Runtime, raw: str | bytes) -> None:
    @retry(
        stop=stop_after_attempt(runtime.cfg.max_retries),
        wait=wait_exponential(
            multiplier=runtime.cfg.retry_backoff_seconds,
            min=runtime.cfg.retry_backoff_seconds,
            max=30,
        ),
        reraise=True,
    )
    def _process() -> None:
        process_message(runtime, raw)

    _process()


def process_message(runtime: Runtime, raw: str | bytes) -> None:
    message = parse_message(raw)
    document = runtime.store.get_document_version(message)
    if document is None:
        logger.warning(
            "document version not found",
            extra={
                "_document_id": message.document_id,
                "_content_hash": message.content_hash,
                "_url": message.url,
            },
        )
        return

    chunks = runtime.chunker.split(document.markdown)
    if not chunks:
        logger.warning(
            "document produced no chunks",
            extra={"_document_id": document.document_id, "_url": document.url},
        )
        return

    collection = runtime.sink.upsert_document(document, chunks)
    logger.info(
        "document vectorized",
        extra={
            "_document_id": document.document_id,
            "_version_id": document.version_id,
            "_url": document.url,
            "_collection": collection,
            "_chunks": len(chunks),
        },
    )


def install_signal_handlers(runtime: Runtime) -> None:
    def stop(_signum: int, _frame: object) -> None:
        runtime.stopping = True
        logger.info("shutdown requested")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


if __name__ == "__main__":
    main()
