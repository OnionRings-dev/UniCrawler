package main

import (
	"context"
	"crypto/sha256"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type store struct {
	pool *pgxpool.Pool
}

type domainRecord struct {
	ID     int64
	Domain string
}

type crawlRun struct {
	ID int64
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
	if err := s.migrate(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return s, nil
}

func (s *store) close() {
	s.pool.Close()
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
`)
	return err
}

func (s *store) upsertDomain(ctx context.Context, domain string) (domainRecord, error) {
	var out domainRecord
	err := s.pool.QueryRow(ctx, `
INSERT INTO domains (domain, created_at, updated_at)
VALUES ($1, now(), now())
ON CONFLICT (domain)
DO UPDATE SET updated_at = now()
RETURNING id, domain
`, domain).Scan(&out.ID, &out.Domain)
	return out, err
}

func (s *store) startRun(ctx context.Context, domainID int64, seedURL string) (crawlRun, error) {
	var run crawlRun
	err := s.pool.QueryRow(ctx, `
INSERT INTO crawl_runs (domain_id, seed_url, status)
VALUES ($1, $2, 'running')
RETURNING id
`, domainID, seedURL).Scan(&run.ID)
	return run, err
}

func (s *store) finishRun(ctx context.Context, runID int64, status string, stats crawlStats) error {
	_, err := s.pool.Exec(ctx, `
UPDATE crawl_runs
SET status = $2,
	finished_at = now(),
	discovered_count = $3,
	page_count = $4,
	error_count = $5
WHERE id = $1
`, runID, status, stats.Links, stats.Pages, stats.Errors)
	return err
}

func (s *store) upsertURLs(ctx context.Context, domainID, runID int64, urls []string) error {
	if len(urls) == 0 {
		return nil
	}

	rows := make([][]any, 0, len(urls))
	for _, raw := range urls {
		sum := sha256.Sum256([]byte(raw))
		hash := make([]byte, len(sum))
		copy(hash, sum[:])
		rows = append(rows, []any{domainID, raw, hash, runID})
	}

	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	_, err = tx.Exec(ctx, `
CREATE TEMP TABLE tmp_urls (
	domain_id BIGINT NOT NULL,
	url TEXT NOT NULL,
	url_hash BYTEA NOT NULL,
	last_crawl_run_id BIGINT NOT NULL
) ON COMMIT DROP
`)
	if err != nil {
		return err
	}

	_, err = tx.CopyFrom(ctx, pgx.Identifier{"tmp_urls"}, []string{"domain_id", "url", "url_hash", "last_crawl_run_id"}, pgx.CopyFromRows(rows))
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
INSERT INTO urls (domain_id, url, url_hash, first_seen_at, last_seen_at, last_crawl_run_id)
SELECT DISTINCT ON (domain_id, url_hash)
	domain_id, url, url_hash, now(), now(), last_crawl_run_id
FROM tmp_urls
ORDER BY domain_id, url_hash, url
ON CONFLICT (domain_id, url_hash)
DO UPDATE SET
	last_seen_at = now(),
	last_crawl_run_id = EXCLUDED.last_crawl_run_id
`)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

func (s *store) replayDomain(ctx context.Context, domain string, batchSize int, handle func([]string) error) (int64, error) {
	var domainID int64
	err := s.pool.QueryRow(ctx, `SELECT id FROM domains WHERE domain = $1`, domain).Scan(&domainID)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return 0, nil
		}
		return 0, err
	}

	rows, err := s.pool.Query(ctx, `
SELECT url
FROM urls
WHERE domain_id = $1
ORDER BY id
`, domainID)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	batch := make([]string, 0, batchSize)
	var total int64
	for rows.Next() {
		var raw string
		if err := rows.Scan(&raw); err != nil {
			return total, err
		}
		batch = append(batch, raw)
		if len(batch) >= batchSize {
			if err := handle(batch); err != nil {
				return total, err
			}
			total += int64(len(batch))
			batch = batch[:0]
		}
	}
	if rows.Err() != nil {
		return total, rows.Err()
	}
	if len(batch) > 0 {
		if err := handle(batch); err != nil {
			return total, err
		}
		total += int64(len(batch))
	}
	return total, nil
}

func runStatus(ctx context.Context) string {
	if ctx.Err() != nil {
		return "interrupted"
	}
	return "completed"
}
