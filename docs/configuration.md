# Configuration

All Compose-facing configuration is declared in `.env.example`.

## Naming

- Infrastructure variables use shared names: `POSTGRES_*`, `REDIS_*`, `QDRANT_*`.
- Service behavior uses prefixes: `MAPPER_*`, `PARSER_*`, `VECTORIZER_*`, `MONITOR_*`.
- Compose maps prefixed values into the generic service env names expected internally, such as `INPUT_QUEUE`, `OUTPUT_QUEUE`, and `WORKERS`.

## Required Admin Settings

The monitor requires `MONITOR_ADMIN_PASSWORD_HASH` before login succeeds.

```sh
printf '%s' 'your-password' | sha256sum
```

Set the first hex field as `MONITOR_ADMIN_PASSWORD_HASH`. Set a unique `MONITOR_SESSION_SECRET` for deployed environments.
