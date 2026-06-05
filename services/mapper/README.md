# UniCrawler Mapper

Mapper consumes `crawl.request.v1` messages, crawls same-domain links, persists normalized URLs in Postgres, and emits `parse.request.v1` messages containing only Postgres IDs needed by the parser.

## Queues

- Input: `MAPPER_INPUT_QUEUE`, default `mapper:in`.
- Output: `MAPPER_OUTPUT_QUEUE`, default `mapper:out`.

Input:

```json
{"type":"crawl.request.v1","version":1,"payload":{"seed_url":"https://example.com/"}}
```

Output:

```json
{"type":"parse.request.v1","version":1,"payload":{"url_id":1,"domain_id":1,"crawl_run_id":1}}
```

## Database

The mapper assumes migrations have already created `domains`, `crawl_runs`, `urls`, and monitoring tables. It must not create or alter schema.
