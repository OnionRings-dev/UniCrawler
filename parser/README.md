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
  "parsed_at": "2026-05-31T12:00:00Z"
}
```

## Rendering Backend

The default backend is custom Go code using `chromedp` and Chromium installed in the parser container. The renderer is behind a small `Renderer` interface, so a future Firecrawl/self-hosted adapter can be added without changing Redis queues, Postgres schema, or downstream consumers.

This keeps Docker Compose simple while still supporting JavaScript-rendered pages. Scale large workloads by tuning `WORKERS`, running multiple parser replicas, and increasing Redis/Postgres capacity.

Direct asset URLs such as images, videos, fonts, archives, feeds, and PDFs are skipped before Chromium. Mapper output can include those when a site links media files from anchors; they are not useful Markdown documents and should not consume render retries.

## Postgres Storage

The parser extends the existing database with:

- `page_documents`: one current document row per normalized URL/domain.
- `page_document_versions`: immutable Markdown versions keyed by content hash.
- `page_parse_errors`: render/extraction failures.

`page_documents.latest_content_hash` points at the last successful content hash. If a later parse produces the same hash, no new version is inserted and nothing is pushed to `parser:out`.

## Configuration

Environment variables:

- `REDIS_ADDR`, default `redis:6379`
- `REDIS_PASSWORD`, default empty
- `REDIS_DB`, default `0`
- `REDIS_POOL_SIZE`, default `WORKERS * 2`
- `POSTGRES_DSN`, default `postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable`
- `INPUT_QUEUE`, default `mapper:out`
- `OUTPUT_QUEUE`, default `parser:out`
- `WORKERS`, default `8`
- `REQUEST_TIMEOUT`, default `15s`; Compose uses `30s` for heavier public sites
- `RENDER_TIMEOUT`, default `30s`; Compose uses `60s` for heavier public sites
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
