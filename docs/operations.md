# Operations

## Start

```sh
docker compose up --build
```

Open the monitor at `http://localhost:${MONITOR_PORT:-8080}`.

## Seed

Use the monitor UI/API. Direct Redis writes must use JSON envelopes, not raw URLs.

```sh
docker compose exec redis redis-cli LPUSH mapper:in \
  '{"type":"crawl.request.v1","version":1,"payload":{"seed_url":"https://example.com/"}}'
```

## Replay

Replay is handled by the monitor or mapper CLI and emits `parse.request.v1` messages from stored Postgres URL IDs.

## Reset Local State

```sh
docker compose down -v
docker compose up --build
```

## Scale Parser

```sh
PARSER_REPLICAS=2 PARSER_WORKERS=4 docker compose up -d --build
```
