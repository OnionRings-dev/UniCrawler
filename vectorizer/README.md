# UniCrawler Vectorizer

Final UniCrawler pipeline node. It consumes changed-document events from Redis, loads the exact Markdown version from Postgres, chunks it for retrieval, embeds the chunks, and upserts them into Qdrant.

## Flow

```text
parser:out -> Postgres page_document_versions -> chunking -> embeddings -> Qdrant
```

`parser:out` messages contain `document_id` and `content_hash`; the vectorizer uses both values to read the immutable Markdown version stored by the parser. Qdrant point IDs are deterministic, so processing the same message again updates the same chunks instead of duplicating them.

## Collections

By default, one Qdrant collection is created per domain:

```text
unicrawler_example_com
```

Set `COLLECTION_SCOPE=host` to split by URL host, or `COLLECTION_SCOPE=document_type` to split a domain into HTML/PDF collections.

Each point payload includes the chunk text plus metadata useful for future RAG citation and filtering: URL, final URL, domain, title, language, document type, source URL, document/version IDs, content hash, chunk index, token offsets, headings, parse timestamp, and embedding model.

## Chunking

The chunker is Markdown-aware:

- preserves heading context in chunk text and payload;
- uses token counts via `tiktoken`;
- keeps configurable overlap for recall across chunk boundaries;
- splits very large blocks without exceeding the target token budget.

Defaults:

- `CHUNK_TOKENS=700`
- `CHUNK_OVERLAP_TOKENS=100`
- `MIN_CHUNK_TOKENS=40`

## Embeddings

Default provider is local FastEmbed:

```text
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
```

For OpenAI embeddings:

```text
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=...
```

Changing embedding model usually changes vector dimensionality. Use a new `COLLECTION_PREFIX` or recreate Qdrant collections when dimensions differ.

## Reliability

The node uses `BRPOPLPUSH` to move messages from `INPUT_QUEUE` to `PROCESSING_QUEUE` before work starts. A message is removed from `PROCESSING_QUEUE` only after Qdrant upsert succeeds. Failed messages are pushed to `FAILED_QUEUE`.

## Configuration

- `REDIS_ADDR`, default `redis:6379`
- `REDIS_PASSWORD`, default empty
- `REDIS_DB`, default `0`
- `INPUT_QUEUE`, default `parser:out`
- `PROCESSING_QUEUE`, default `vectorizer:processing`
- `FAILED_QUEUE`, default `vectorizer:failed`
- `QUEUE_BLOCK_TIME`, default `5`
- `REDIS_SOCKET_TIMEOUT`, default `QUEUE_BLOCK_TIME + 10`
- `POSTGRES_DSN`, default `postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable`
- `QDRANT_URL`, default `http://qdrant:6333`
- `QDRANT_API_KEY`, optional
- `COLLECTION_PREFIX`, default `unicrawler`
- `COLLECTION_SCOPE`, default `domain`
- `EMBEDDING_PROVIDER`, default `fastembed`
- `EMBEDDING_MODEL`, provider-specific default
- `BATCH_SIZE`, default `32`
- `MAX_RETRIES`, default `3`

## Local Development

```sh
cd vectorizer
uv sync
uv run pytest
uv run vectorizer
```
