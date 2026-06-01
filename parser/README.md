# UniCrawler Parser

JavaScript-capable content parser for the UniCrawler pipeline. It consumes normalized URL strings from Redis, renders each page in headless Chromium, extracts the main readable content, converts it to Markdown, versions changed documents in Postgres, and pushes changed documents to the next queue.

## Queues

- Input: `mapper:out`
- Output: `parser:out`

Input items are plain URL strings produced by the mapper:

```text
https://example.com/products?page=2
```

Output items are JSON and are only emitted for new or changed content:

```json
{
  "url": "https://example.com/products?page=2",
  "domain": "example.com",
  "document_id": 123,
  "content_hash": "bf2c...",
  "changed": true,
  "parsed_at": "2026-05-31T12:00:00Z",
  "document_type": "html"
}
```

PDF output includes the original PDF URL in `url` and the page where that PDF link was found in `source_url`:

```json
{
  "url": "https://example.com/files/menu.pdf",
  "domain": "example.com",
  "document_id": 456,
  "content_hash": "ab12...",
  "changed": true,
  "parsed_at": "2026-05-31T12:01:00Z",
  "document_type": "pdf",
  "source_url": "https://example.com/menu"
}
```

## Rendering Backend

The default backend is custom Go code using `chromedp` and Chromium installed in the parser container. The renderer is behind a small `Renderer` interface, so a future Firecrawl/self-hosted adapter can be added without changing Redis queues, Postgres schema, or downstream consumers.

This keeps Docker Compose simple while still supporting JavaScript-rendered pages. Scale large workloads by tuning `WORKERS`, running multiple parser replicas, and increasing Redis/Postgres capacity.

Direct asset URLs such as images, videos, fonts, archives, feeds, and Office files are skipped before Chromium. PDF URLs are handled separately: the parser fetches the PDF, extracts text, stores Markdown, and records the page URL where the PDF was found when it was discovered from rendered HTML.

## Postgres Storage

The parser extends the existing database with:

- `page_documents`: one current document row per normalized URL/domain.
- `page_document_versions`: immutable Markdown versions keyed by content hash.
- `page_document_sources`: source pages where linked documents such as PDFs were found.
- `page_parse_errors`: render/extraction failures.

`page_documents.latest_content_hash` points at the last successful content hash. If a later parse produces the same hash, no new version is inserted and nothing is pushed to `parser:out`.

## Configuration

Environment variables:

- `REDIS_ADDR`, default `redis:6379` (parameterized in `docker-compose.yml`)
- `REDIS_PASSWORD`, default empty
- `REDIS_DB`, default `0`
- `REDIS_POOL_SIZE`, default `WORKERS * 2`
- `POSTGRES_DSN`, default `postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable` (configured via env in `docker-compose.yml`)
- `INPUT_QUEUE`, default `mapper:out`
- `OUTPUT_QUEUE`, default `parser:out`
- `WORKERS`, default `8`
- `PARSER_REPLICAS`, Compose-only parser replica count, default `1`
- `PARSER_WORKERS`, Compose-only `WORKERS` value per parser replica, default `4`
- `PARSER_REDIS_POOL_SIZE`, Compose-only Redis pool size per parser replica, default `8`
- `REQUEST_TIMEOUT`, default `15s`; Compose uses `30s` for heavier public sites
- `RENDER_TIMEOUT`, default `30s`; Compose uses `60s` for heavier public sites
- `MAX_PDF_BYTES`, default `52428800`
- `MAX_RETRIES`, default `2`
- `USER_AGENT`, default `UniCrawlerParser/0.1`
- `QUEUE_BLOCK_TIME`, default `5s`
- `CHROME_PATH`, optional path to Chromium
- `RENDER_REMOTE_DEBUG_URL`, optional remote CDP endpoint for a replaceable renderer deployment

## Manual Test

Start the stack:

```sh
docker compose up --build
```

By default Compose starts 1 parser replica with 4 workers. Total parser concurrency is roughly:

```text
PARSER_REPLICAS * PARSER_WORKERS
```

Override it when starting the stack:

```sh
PARSER_REPLICAS=2 PARSER_WORKERS=4 PARSER_REDIS_POOL_SIZE=8 docker compose up -d --build
```

Increase gradually. Chromium is memory-heavy, and too many tabs can make renders slower rather than faster. You can also change only the replica count at runtime:

```sh
docker compose up -d --scale parser=2
```

Push a URL directly into the parser input queue:

```sh
docker compose exec redis redis-cli LPUSH mapper:out https://example.com/
```

Read the parser result:

```sh
docker compose exec redis redis-cli BRPOP parser:out 0
```

Or run the full pipeline from mapper seed to parser output:

```sh
docker compose exec redis redis-cli LPUSH mapper:in https://example.com/
docker compose exec redis redis-cli BRPOP parser:out 0
```

Run tests locally:

```sh
cd parser
go test ./...
```
