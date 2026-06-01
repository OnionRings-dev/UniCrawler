# UniCrawler

Redis-driven crawling pipeline with a mapper, parser, and vectorizer backed by Postgres for durable storage and Qdrant for retrieval-ready embeddings. The repo ships with a Docker Compose stack that wires Postgres, Redis, Qdrant, and the pipeline nodes together.

## Components

- **Mapper** (`mapper/`): crawls a seed URL, de-duplicates same-domain links, stores sitemaps in Postgres, and publishes URLs to `mapper:out`. See [mapper/README.md](mapper/README.md).
- **Parser** (`parser/`): renders pages (including JS), extracts readable content, versions Markdown in Postgres, and publishes changed documents to `parser:out`. See [parser/README.md](parser/README.md).
- **Vectorizer** (`vectorizer/`): consumes changed parser events, loads Markdown versions from Postgres, chunks and embeds them, and upserts retrieval-ready points into Qdrant collections per endpoint/domain. See [vectorizer/README.md](vectorizer/README.md).
- **Monitor** (`monitor/`): small FastAPI web/API service for queue status, domain/run inspection, mapper enqueue, and replay of already mapped domains.

## Data flow

`mapper:in` → `mapper:out` → `parser:out` → `Qdrant`

## Quick start

```sh
docker compose up --build
```

Open the monitor at [http://localhost:8080](http://localhost:8080). It shows Redis queue lengths, Postgres counters, recent domains/runs, and forms for adding seeds or replaying a mapped domain into the parser queue.

Seed a crawl:

```sh
docker compose exec redis redis-cli LPUSH mapper:in https://example.com/
```

Or use the monitor API:

```sh
curl -X POST http://localhost:8080/api/enqueue \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/"}'
```

Replay a stored sitemap into `mapper:out` without crawling again:

```sh
curl -X POST http://localhost:8080/api/replay \
  -H 'content-type: application/json' \
  -d '{"domain":"example.com"}'
```

Read parsed output before the vectorizer consumes it:

```sh
docker compose exec redis redis-cli BRPOP parser:out 0
```

`parser:out` emits JSON messages for new or changed documents (see the parser README for the schema). The vectorizer consumes those messages by default and writes chunks into Qdrant collections named like `unicrawler_example_com`.

## Configuration

Most settings are environment variables in `docker-compose.yml`. You can use a `.env` file (see `.env.example`) or override them at runtime:

```sh
# Scaling and processing
PARSER_REPLICAS=2 PARSER_WORKERS=4 PARSER_REDIS_POOL_SIZE=8 docker compose up -d --build

# Database settings
POSTGRES_DB=my_db POSTGRES_PORT=5433 docker compose up -d

# Redis settings
REDIS_PORT=6380 REDIS_PASSWORD=secret docker compose up -d

# Monitor settings
MONITOR_PORT=8081 docker compose up -d

# OpenAI embeddings instead of local FastEmbed
EMBEDDING_PROVIDER=openai EMBEDDING_MODEL=text-embedding-3-small OPENAI_API_KEY=... docker compose up -d --build
```

## Storage

Postgres stores crawled sitemaps and parsed document versions in the `unicrawler` database. Qdrant stores vector collections partitioned by endpoint/domain for future RAG retrieval. Docker Compose provisions persistent volumes for Postgres, Redis, Qdrant, and the vectorizer model cache.
