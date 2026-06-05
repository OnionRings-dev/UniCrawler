# Monitoring

The monitor is an authenticated FastAPI admin surface.

## Admin Workflows

- Add seed links to `mapper:in`.
- Replay mapped domains into `mapper:out`.
- View queue lengths and stage ETA.
- View domain, run, Postgres, Qdrant, and node heartbeat summaries.

## Telemetry

Nodes expose:

- `/healthz`: liveness.
- `/metrics`: Prometheus-style uptime metric.

Nodes also write durable rows to:

- `pipeline_events` for start/finish/change events.
- `node_heartbeats` for last-seen node status.
- `node_metrics` for future numeric samples.

ETA is currently computed from recent queue drain rates and recent table/event counts.
