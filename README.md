# UniCrawler

Redis-driven website ingestion pipeline backed by Postgres for durable state and Qdrant for retrieval-ready vectors.

## Layout

- `services/mapper`: crawl discovery and URL persistence.
- `services/parser`: rendering, content extraction, and document versioning.
- `services/vectorizer`: chunking, embeddings, and Qdrant upserts.
- `services/monitor`: authenticated admin UI/API.
- `db/migrations`: Goose-managed Postgres schema.
- `db/queries`: SQLC query definitions.
- `packages/go/shared`: Go queue contracts.
- `packages/go/dbgen`: SQLC-generated Go database package.
- `packages/python/shared`: Python contracts and typed row helpers.
- `docs`: architecture, configuration, database, queues, monitoring, and operations docs.

## Start

Copy `.env.example` to `.env`, set `MONITOR_ADMIN_PASSWORD_HASH` and `MONITOR_SESSION_SECRET`, then run:

```sh
docker compose up --build
```

Open `http://localhost:8080`.

## Queue Contract

Redis queues use versioned JSON envelopes. Raw URL list items are no longer accepted by the pipeline nodes.

```json
{"type":"crawl.request.v1","version":1,"payload":{"seed_url":"https://example.com/"}}
```

See `docs/queues.md` for all queue schemas.

## Database

Pipeline nodes no longer create tables. The `db-migrate` Compose service applies Goose migrations before worker services start. See `docs/database.md`.
