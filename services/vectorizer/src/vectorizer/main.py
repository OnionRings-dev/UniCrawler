from __future__ import annotations

import json
import logging
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from threading import Thread

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from tenacity import retry, stop_after_attempt, wait_exponential

from vectorizer.chunking import MarkdownChunker
from vectorizer.config import Config, load_config
from vectorizer.embeddings import make_embedder
from vectorizer.hierarchy import PreparedChunks, prepare_chunks
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
        start_health_server(cfg.http_addr)
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
        store.heartbeat("vectorizer", "ready", {"input_queue": cfg.input_queue, "processing_queue": cfg.processing_queue})
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
    except Exception as exc:
        logger.exception("message failed", extra={"_raw": raw_value})
        runtime.redis.lrem(runtime.cfg.processing_queue, 1, raw_value)
        runtime.redis.lpush(
            runtime.cfg.failed_queue,
            json.dumps(
                {
                    "type": "dead_letter.v1",
                    "version": 1,
                    "payload": {
                        "original": raw_value,
                        "service": "vectorizer",
                        "error": str(exc),
                        "attempt": runtime.cfg.max_retries,
                        "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                },
                ensure_ascii=False,
            ),
        )
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
    runtime.store.pipeline_event("vectorizer", "vectorize_started", message.document_id, message.version_id)
    document = runtime.store.get_document_version(message)
    if document is None:
        logger.warning(
            "document version not found",
            extra={
                "_document_id": message.document_id,
                "_version_id": message.version_id,
            },
        )
        return

    prepared = prepare_chunks(document.markdown, runtime.chunker, runtime.cfg)
    if not prepared.chunks:
        logger.warning(
            "document produced no chunks",
            extra={"_document_id": document.document_id, "_url": document.url},
        )
        return

    if prepared.dedup.removed_blocks > 0:
        logger.info(
            "document markdown deduplicated",
            extra={
                "_document_id": document.document_id,
                "_url": document.url,
                "_original_blocks": prepared.dedup.original_blocks,
                "_removed_blocks": prepared.dedup.removed_blocks,
                "_original_chars": prepared.dedup.original_chars,
                "_deduped_chars": prepared.dedup.deduped_chars,
            },
        )

    if prepared.oversized:
        logger.warning(
            "document is oversized; using hierarchical indexing",
            extra={
                "_document_id": document.document_id,
                "_url": document.url,
                "_original_chunks": prepared.original_chunk_count,
                "_indexed_content_chunks": prepared.indexed_content_chunks,
                "_indexed_summary_chunks": prepared.indexed_summary_chunks,
                "_indexed_chunks": len(prepared.chunks),
            },
        )

    collection = runtime.sink.upsert_document(document, prepared.chunks)
    if prepared.oversized:
        publish_oversized_event(runtime, prepared, document.url, document.document_id)
    logger.info(
        "document vectorized",
        extra={
            "_document_id": document.document_id,
            "_version_id": document.version_id,
            "_url": document.url,
            "_collection": collection,
            "_chunks": len(prepared.chunks),
            "_oversized": prepared.oversized,
        },
    )
    runtime.store.pipeline_event(
        "vectorizer",
        "vectorize_finished",
        document.document_id,
        document.version_id,
        {"url": document.url, "chunks": len(prepared.chunks), "collection": collection},
    )


def start_health_server(addr: str) -> None:
    if not addr:
        return
    host, _, port_raw = addr.rpartition(":")
    host = host or "0.0.0.0"
    port = int(port_raw or "8081")
    started = time.time()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return
            if self.path == "/metrics":
                self.send_response(200)
                self.send_header("content-type", "text/plain; version=0.0.4")
                self.end_headers()
                uptime = int(time.time() - started)
                self.wfile.write(f'unicrawler_node_uptime_seconds{{service="vectorizer"}} {uptime}\n'.encode())
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()


def publish_oversized_event(
    runtime: Runtime,
    prepared: PreparedChunks,
    url: str,
    document_id: int,
) -> None:
    payload = {
        "document_id": document_id,
        "url": url,
        "original_chunks": prepared.original_chunk_count,
        "indexed_content_chunks": prepared.indexed_content_chunks,
        "indexed_summary_chunks": prepared.indexed_summary_chunks,
        "indexed_chunks": len(prepared.chunks),
        "original_chars": prepared.dedup.original_chars,
        "deduped_chars": prepared.dedup.deduped_chars,
        "removed_blocks": prepared.dedup.removed_blocks,
    }
    try:
        runtime.redis.lpush(runtime.cfg.oversized_queue, json.dumps(payload, ensure_ascii=False))
    except RedisError:
        logger.warning("oversized audit event publish failed", exc_info=True)


def install_signal_handlers(runtime: Runtime) -> None:
    def stop(_signum: int, _frame: object) -> None:
        runtime.stopping = True
        logger.info("shutdown requested")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


if __name__ == "__main__":
    main()
