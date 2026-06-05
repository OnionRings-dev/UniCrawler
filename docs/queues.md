# Redis Queue Contracts

Queues use Redis lists and JSON envelopes:

```json
{"type":"example.v1","version":1,"payload":{}}
```

## `mapper:in`

Type: `crawl.request.v1`

```json
{"type":"crawl.request.v1","version":1,"payload":{"seed_url":"https://example.com/"}}
```

## `mapper:out`

Type: `parse.request.v1`

```json
{"type":"parse.request.v1","version":1,"payload":{"url_id":10,"domain_id":2,"crawl_run_id":5}}
```

## `parser:out`

Type: `vectorize.request.v1`

```json
{"type":"vectorize.request.v1","version":1,"payload":{"document_id":22,"version_id":31}}
```

Failed queues should preserve the original message with `service`, `error`, `attempt`, and `failed_at` metadata.
