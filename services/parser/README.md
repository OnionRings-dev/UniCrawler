# UniCrawler Parser

Parser consumes `parse.request.v1`, loads the URL from Postgres, renders/extracts content, stores document versions, and emits `vectorize.request.v1`.

## Queues

Input:

```json
{"type":"parse.request.v1","version":1,"payload":{"url_id":1,"domain_id":1,"crawl_run_id":1}}
```

Output:

```json
{"type":"vectorize.request.v1","version":1,"payload":{"document_id":10,"version_id":12}}
```

## Database

The parser writes `page_documents`, `page_document_versions`, `page_document_sources`, `page_parse_errors`, and progress events. Schema changes belong in `db/migrations`.

## Local Tests

```sh
cd services/parser
go test ./...
```
