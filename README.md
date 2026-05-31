# UniCrawler

Redis-driven crawling pipeline with a mapper and parser backed by Postgres for durable storage. The repo ships with a Docker Compose stack that wires Postgres, Redis, the mapper, and the parser together.

## Components

- **Mapper** (`mapper/`): crawls a seed URL, de-duplicates same-domain links, stores sitemaps in Postgres, and publishes URLs to `mapper:out`. See [mapper/README.md](mapper/README.md).
- **Parser** (`parser/`): renders pages (including JS), extracts readable content, versions Markdown in Postgres, and publishes changed documents to `parser:out`. See [parser/README.md](parser/README.md).

## Data flow

`mapper:in` → `mapper:out` → `parser:out`

## Quick start

```sh
docker compose up --build
```

Seed a crawl:

```sh
docker compose exec redis redis-cli LPUSH mapper:in https://example.com/
```

Read parsed output:

```sh
docker compose exec redis redis-cli BRPOP parser:out 0
```

`parser:out` emits JSON messages for new or changed documents (see the parser README for the schema).

## Configuration

Most settings are environment variables in `docker-compose.yml`. Override at runtime as needed:

```sh
PARSER_REPLICAS=2 PARSER_WORKERS=4 PARSER_REDIS_POOL_SIZE=8 docker compose up -d --build
```

## Storage

Postgres stores crawled sitemaps and parsed document versions in the `unicrawler` database. Docker Compose provisions persistent volumes for both Postgres and Redis.
