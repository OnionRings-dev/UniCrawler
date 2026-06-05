# UniCrawler Architecture

UniCrawler is a Docker Compose pipeline for ingesting large websites into Postgres and Qdrant.

## Services

- `services/mapper`: consumes `crawl.request.v1`, crawls same-domain links, stores URL rows, and emits `parse.request.v1`.
- `services/parser`: consumes `parse.request.v1`, loads URL metadata from Postgres, renders/extracts content, writes document versions, and emits `vectorize.request.v1`.
- `services/vectorizer`: consumes `vectorize.request.v1`, loads immutable document versions from Postgres, chunks/embeds content, and upserts Qdrant points.
- `services/monitor`: authenticated admin UI/API for enqueue, replay, queue flow, ETA, and storage summaries.
- `db-migrate`: one-shot Goose migration container. Pipeline nodes depend on it and never create tables.

Shared contracts live in `packages/go/shared` and `packages/python/shared`. SQLC-generated Go DB access lives in `packages/go/dbgen`.

## Flow

```text
monitor/admin -> mapper:in -> mapper -> mapper:out -> parser -> parser:out -> vectorizer -> Qdrant
                                  \              \                 \
                                   Postgres       Postgres          Postgres events
```

Redis carries only minimal work references. Postgres is the durable source of truth for URLs, documents, versions, progress events, and node status.
