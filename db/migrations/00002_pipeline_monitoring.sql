-- +goose Up
CREATE TABLE IF NOT EXISTS pipeline_events (
	id BIGSERIAL PRIMARY KEY,
	service TEXT NOT NULL,
	event_type TEXT NOT NULL,
	domain_id BIGINT REFERENCES domains(id) ON DELETE SET NULL,
	crawl_run_id BIGINT REFERENCES crawl_runs(id) ON DELETE SET NULL,
	url_id BIGINT REFERENCES urls(id) ON DELETE SET NULL,
	document_id BIGINT REFERENCES page_documents(id) ON DELETE SET NULL,
	version_id BIGINT REFERENCES page_document_versions(id) ON DELETE SET NULL,
	payload JSONB NOT NULL DEFAULT '{}'::jsonb,
	created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS node_heartbeats (
	service TEXT PRIMARY KEY,
	status TEXT NOT NULL,
	details JSONB NOT NULL DEFAULT '{}'::jsonb,
	last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS node_metrics (
	id BIGSERIAL PRIMARY KEY,
	service TEXT NOT NULL,
	metric_name TEXT NOT NULL,
	metric_value DOUBLE PRECISION NOT NULL,
	labels JSONB NOT NULL DEFAULT '{}'::jsonb,
	recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_service_created_at ON pipeline_events(service, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_type_created_at ON pipeline_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_node_metrics_service_recorded_at ON node_metrics(service, recorded_at DESC);

-- +goose Down
DROP TABLE IF EXISTS node_metrics;
DROP TABLE IF EXISTS node_heartbeats;
DROP TABLE IF EXISTS pipeline_events;
