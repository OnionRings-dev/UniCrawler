package main

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type store struct {
	pool *pgxpool.Pool
}

type pageRecord struct {
	URL          string
	URLHash      []byte
	Domain       string
	Title        string
	Language     string
	Markdown     string
	ContentHash  []byte
	HTMLHash     []byte
	StatusCode   int
	ContentType  string
	FinalURL     string
	ParsedAt     time.Time
	DocumentType string
	SourceURL    string
}

type saveResult struct {
	DocumentID int64
	VersionID  int64
	Changed    bool
}

func openStore(ctx context.Context, dsn string) (*store, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	s := &store{pool: pool}
	if err := s.migrateWithLock(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return s, nil
}

func (s *store) close() {
	s.pool.Close()
}

func (s *store) migrateWithLock(ctx context.Context) error {
	const lockID int64 = 7741151101
	if _, err := s.pool.Exec(ctx, `SELECT pg_advisory_lock($1)`, lockID); err != nil {
		return err
	}
	defer s.pool.Exec(context.Background(), `SELECT pg_advisory_unlock($1)`, lockID)
	return s.migrate(ctx)
}

func (s *store) migrate(ctx context.Context) error {
	_, err := s.pool.Exec(ctx, `
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

DO $$
BEGIN
	IF NOT EXISTS (
		SELECT 1 FROM pg_constraint WHERE conname = 'page_documents_latest_version_fk'
	) THEN
		ALTER TABLE page_documents
			ADD CONSTRAINT page_documents_latest_version_fk
			FOREIGN KEY (latest_version_id) REFERENCES page_document_versions(id) ON DELETE SET NULL;
	END IF;
END $$;

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

ALTER TABLE page_documents ADD COLUMN IF NOT EXISTS document_type TEXT NOT NULL DEFAULT 'html';
ALTER TABLE page_documents ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE page_document_versions ADD COLUMN IF NOT EXISTS document_type TEXT NOT NULL DEFAULT 'html';
ALTER TABLE page_document_versions ADD COLUMN IF NOT EXISTS source_url TEXT;
`)
	return err
}

func (s *store) saveParsedPage(ctx context.Context, record pageRecord) (saveResult, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return saveResult{}, err
	}
	defer tx.Rollback(ctx)

	domainID, err := upsertDomain(ctx, tx, record.Domain)
	if err != nil {
		return saveResult{}, err
	}
	urlID, err := lookupURLID(ctx, tx, domainID, record.URLHash)
	if err != nil {
		return saveResult{}, err
	}

	var documentID int64
	var latestContentHash []byte
	err = tx.QueryRow(ctx, `
INSERT INTO page_documents (
	domain_id, url_id, url, url_hash, final_url, title, language, content_type,
	status_code, document_type, source_url, latest_content_hash, latest_html_hash, last_parsed_at, last_error,
	created_at, updated_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NULL, NULL, $12, NULL, now(), now())
ON CONFLICT (domain_id, url_hash)
DO UPDATE SET
	url_id = COALESCE(EXCLUDED.url_id, page_documents.url_id),
	url = EXCLUDED.url,
	final_url = EXCLUDED.final_url,
	title = EXCLUDED.title,
	language = EXCLUDED.language,
	content_type = EXCLUDED.content_type,
	status_code = EXCLUDED.status_code,
	document_type = EXCLUDED.document_type,
	source_url = COALESCE(EXCLUDED.source_url, page_documents.source_url),
	last_parsed_at = EXCLUDED.last_parsed_at,
	last_error = NULL,
	updated_at = now()
RETURNING id, latest_content_hash
`, domainID, urlID, record.URL, record.URLHash, record.FinalURL, record.Title, record.Language, record.ContentType, record.StatusCode, documentType(record.DocumentType), nullEmpty(record.SourceURL), record.ParsedAt).Scan(&documentID, &latestContentHash)
	if err != nil {
		return saveResult{}, err
	}
	if err := upsertDocumentSource(ctx, tx, documentID, record.SourceURL, record.URL); err != nil {
		return saveResult{}, err
	}

	if !contentChanged(latestContentHash, record.ContentHash) {
		_, err = tx.Exec(ctx, `
UPDATE page_documents
SET latest_html_hash = $2,
	last_parsed_at = $3,
	last_error = NULL,
	updated_at = now()
WHERE id = $1
`, documentID, record.HTMLHash, record.ParsedAt)
		if err != nil {
			return saveResult{}, err
		}
		if err := tx.Commit(ctx); err != nil {
			return saveResult{}, err
		}
		return saveResult{DocumentID: documentID, Changed: false}, nil
	}

	var versionID int64
	err = tx.QueryRow(ctx, `
INSERT INTO page_document_versions (
	document_id, content_hash, html_hash, title, language, markdown,
	status_code, content_type, final_url, document_type, source_url, parsed_at, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())
ON CONFLICT (document_id, content_hash)
DO UPDATE SET parsed_at = EXCLUDED.parsed_at
RETURNING id
`, documentID, record.ContentHash, record.HTMLHash, record.Title, record.Language, record.Markdown, record.StatusCode, record.ContentType, record.FinalURL, documentType(record.DocumentType), nullEmpty(record.SourceURL), record.ParsedAt).Scan(&versionID)
	if err != nil {
		return saveResult{}, err
	}

	_, err = tx.Exec(ctx, `
UPDATE page_documents
SET latest_content_hash = $2,
	latest_html_hash = $3,
	latest_version_id = $4,
	last_parsed_at = $5,
	last_error = NULL,
	updated_at = now()
WHERE id = $1
`, documentID, record.ContentHash, record.HTMLHash, versionID, record.ParsedAt)
	if err != nil {
		return saveResult{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return saveResult{}, err
	}
	return saveResult{DocumentID: documentID, VersionID: versionID, Changed: true}, nil
}

func (s *store) recordParseError(ctx context.Context, rawURL string, urlHash []byte, domain string, message string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	domainID, err := upsertDomain(ctx, tx, domain)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
INSERT INTO page_parse_errors (domain_id, url, url_hash, error, created_at)
VALUES ($1, $2, $3, $4, now())
`, domainID, rawURL, urlHash, message)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
UPDATE page_documents
SET last_error = $3,
	last_parsed_at = now(),
	updated_at = now()
WHERE domain_id = $1 AND url_hash = $2
`, domainID, urlHash, message)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

type queryer interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

type execer interface {
	Exec(context.Context, string, ...any) (pgconn.CommandTag, error)
}

func upsertDomain(ctx context.Context, q queryer, domain string) (int64, error) {
	var id int64
	err := q.QueryRow(ctx, `
INSERT INTO domains (domain, created_at, updated_at)
VALUES ($1, now(), now())
ON CONFLICT (domain)
DO UPDATE SET updated_at = now()
RETURNING id
`, domain).Scan(&id)
	return id, err
}

func upsertDocumentSource(ctx context.Context, tx interface {
	execer
}, documentID int64, sourceURL string, linkURL string) error {
	if sourceURL == "" {
		return nil
	}
	_, err := tx.Exec(ctx, `
INSERT INTO page_document_sources (document_id, source_url, link_url, first_seen_at, last_seen_at)
VALUES ($1, $2, $3, now(), now())
ON CONFLICT (document_id, source_url)
DO UPDATE SET
	link_url = EXCLUDED.link_url,
	last_seen_at = now()
`, documentID, sourceURL, linkURL)
	return err
}

func documentType(value string) string {
	value = strings.TrimSpace(strings.ToLower(value))
	if value == "" {
		return "html"
	}
	return value
}

func nullEmpty(value string) *string {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	return &value
}

func lookupURLID(ctx context.Context, q queryer, domainID int64, urlHash []byte) (*int64, error) {
	var id int64
	err := q.QueryRow(ctx, `SELECT id FROM urls WHERE domain_id = $1 AND url_hash = $2`, domainID, urlHash).Scan(&id)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &id, nil
}

func contentChanged(previous, current []byte) bool {
	return len(previous) == 0 || !bytes.Equal(previous, current)
}
