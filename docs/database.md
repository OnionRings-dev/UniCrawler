# Database

Schema ownership lives in `db/migrations`.

## Migration Workflow

1. Add a new Goose SQL migration under `db/migrations`.
2. Keep existing migrations immutable once shared.
3. Put reusable or typed SQL in `db/queries`.
4. Run SQLC generation from the repository root:

```sh
sqlc generate
```

5. Review generated code in `packages/go/dbgen`.

The `db-migrate` Compose service bakes migrations into its image and runs them against Postgres before mapper/parser/vectorizer/monitor start.

## Tables

Core ingestion tables are `domains`, `crawl_runs`, `urls`, `page_documents`, `page_document_versions`, `page_document_sources`, and `page_parse_errors`.

Monitoring tables are `pipeline_events`, `node_heartbeats`, and `node_metrics`.
