-- +goose Up
CREATE TABLE IF NOT EXISTS domains (
	id BIGSERIAL PRIMARY KEY,
	domain TEXT NOT NULL UNIQUE,
	created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crawl_runs (
	id BIGSERIAL PRIMARY KEY,
	domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
	seed_url TEXT NOT NULL,
	status TEXT NOT NULL,
	started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	finished_at TIMESTAMPTZ,
	discovered_count BIGINT NOT NULL DEFAULT 0,
	page_count BIGINT NOT NULL DEFAULT 0,
	error_count BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS urls (
	id BIGSERIAL PRIMARY KEY,
	domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
	url TEXT NOT NULL,
	url_hash BYTEA NOT NULL,
	first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	last_crawl_run_id BIGINT REFERENCES crawl_runs(id) ON DELETE SET NULL,
	UNIQUE (domain_id, url_hash)
);

CREATE INDEX IF NOT EXISTS idx_urls_domain_id_id ON urls(domain_id, id);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_domain_id_started_at ON crawl_runs(domain_id, started_at DESC);

CREATE TABLE IF NOT EXISTS page_documents (
	id BIGSERIAL PRIMARY KEY,
	domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
	url_id BIGINT REFERENCES urls(id) ON DELETE SET NULL,
	url TEXT NOT NULL,
	url_hash BYTEA NOT NULL,
	final_url TEXT,
	title TEXT,
	language TEXT,
	content_type TEXT,
	status_code INTEGER,
	document_type TEXT NOT NULL DEFAULT 'html',
	source_url TEXT,
	latest_content_hash BYTEA,
	latest_html_hash BYTEA,
	latest_version_id BIGINT,
	last_parsed_at TIMESTAMPTZ,
	last_error TEXT,
	created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	UNIQUE (domain_id, url_hash)
);

CREATE TABLE IF NOT EXISTS page_document_versions (
	id BIGSERIAL PRIMARY KEY,
	document_id BIGINT NOT NULL REFERENCES page_documents(id) ON DELETE CASCADE,
	content_hash BYTEA NOT NULL,
	html_hash BYTEA,
	title TEXT,
	language TEXT,
	markdown TEXT NOT NULL,
	status_code INTEGER,
	content_type TEXT,
	final_url TEXT,
	document_type TEXT NOT NULL DEFAULT 'html',
	source_url TEXT,
	parsed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	UNIQUE (document_id, content_hash)
);

ALTER TABLE page_documents
	ADD CONSTRAINT page_documents_latest_version_fk
	FOREIGN KEY (latest_version_id) REFERENCES page_document_versions(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS page_parse_errors (
	id BIGSERIAL PRIMARY KEY,
	domain_id BIGINT REFERENCES domains(id) ON DELETE SET NULL,
	url TEXT NOT NULL,
	url_hash BYTEA NOT NULL,
	error TEXT NOT NULL,
	created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS page_document_sources (
	id BIGSERIAL PRIMARY KEY,
	document_id BIGINT NOT NULL REFERENCES page_documents(id) ON DELETE CASCADE,
	source_url TEXT NOT NULL,
	link_url TEXT NOT NULL,
	first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	UNIQUE (document_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_page_documents_domain_id_id ON page_documents(domain_id, id);
CREATE INDEX IF NOT EXISTS idx_page_document_versions_document_id_created_at ON page_document_versions(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_parse_errors_url_hash_created_at ON page_parse_errors(url_hash, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_document_sources_source_url ON page_document_sources(source_url);

-- +goose Down
DROP TABLE IF EXISTS page_document_sources;
DROP TABLE IF EXISTS page_parse_errors;
ALTER TABLE IF EXISTS page_documents DROP CONSTRAINT IF EXISTS page_documents_latest_version_fk;
DROP TABLE IF EXISTS page_document_versions;
DROP TABLE IF EXISTS page_documents;
DROP TABLE IF EXISTS urls;
DROP TABLE IF EXISTS crawl_runs;
DROP TABLE IF EXISTS domains;
