package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"unicrawler/dbgen"
)

type store struct {
	pool *pgxpool.Pool
	q    *dbgen.Queries
}

type domainRecord struct {
	ID     int64
	Domain string
}

type crawlRun struct {
	ID int64
}

type urlRecord struct {
	ID             int64
	DomainID       int64
	URL            string
	LastCrawlRunID int64
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
	return &store{pool: pool, q: dbgen.New(pool)}, nil
}

func (s *store) close() {
	s.pool.Close()
}

func (s *store) heartbeat(ctx context.Context, service string, status string, details map[string]any) error {
	payload, err := json.Marshal(details)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `
INSERT INTO node_heartbeats (service, status, details, last_seen_at)
VALUES ($1, $2, $3, now())
ON CONFLICT (service)
DO UPDATE SET status = EXCLUDED.status, details = EXCLUDED.details, last_seen_at = now()
`, service, status, payload)
	return err
}

func (s *store) pipelineEvent(ctx context.Context, service string, eventType string, domainID, crawlRunID, urlID int64, payload map[string]any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `
INSERT INTO pipeline_events (service, event_type, domain_id, crawl_run_id, url_id, payload)
VALUES ($1, $2, NULLIF($3, 0), NULLIF($4, 0), NULLIF($5, 0), $6)
`, service, eventType, domainID, crawlRunID, urlID, body)
	return err
}

func (s *store) upsertDomain(ctx context.Context, domain string) (domainRecord, error) {
	out, err := s.q.UpsertDomain(ctx, domain)
	return domainRecord{ID: out.ID, Domain: out.Domain}, err
}

func (s *store) startRun(ctx context.Context, domainID int64, seedURL string) (crawlRun, error) {
	id, err := s.q.StartCrawlRun(ctx, dbgen.StartCrawlRunParams{DomainID: domainID, SeedUrl: seedURL})
	return crawlRun{ID: id}, err
}

func (s *store) finishRun(ctx context.Context, runID int64, status string, stats crawlStats) error {
	return s.q.FinishCrawlRun(ctx, dbgen.FinishCrawlRunParams{
		ID:              runID,
		Status:          status,
		DiscoveredCount: stats.Links,
		PageCount:       stats.Pages,
		ErrorCount:      stats.Errors,
	})
}

func (s *store) upsertURLs(ctx context.Context, domainID, runID int64, urls []string) ([]urlRecord, error) {
	if len(urls) == 0 {
		return nil, nil
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
		return nil, err
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
		return nil, err
	}

	_, err = tx.CopyFrom(ctx, pgx.Identifier{"tmp_urls"}, []string{"domain_id", "url", "url_hash", "last_crawl_run_id"}, pgx.CopyFromRows(rows))
	if err != nil {
		return nil, err
	}

	rowsOut, err := tx.Query(ctx, `
INSERT INTO urls (domain_id, url, url_hash, first_seen_at, last_seen_at, last_crawl_run_id)
SELECT DISTINCT ON (domain_id, url_hash)
	domain_id, url, url_hash, now(), now(), last_crawl_run_id
FROM tmp_urls
ORDER BY domain_id, url_hash, url
ON CONFLICT (domain_id, url_hash)
DO UPDATE SET
	last_seen_at = now(),
	last_crawl_run_id = EXCLUDED.last_crawl_run_id
RETURNING id, domain_id, url, COALESCE(last_crawl_run_id, 0)
`)
	if err != nil {
		return nil, err
	}
	defer rowsOut.Close()

	out := make([]urlRecord, 0, len(urls))
	for rowsOut.Next() {
		var record urlRecord
		if err := rowsOut.Scan(&record.ID, &record.DomainID, &record.URL, &record.LastCrawlRunID); err != nil {
			return nil, err
		}
		out = append(out, record)
	}
	if rowsOut.Err() != nil {
		return nil, rowsOut.Err()
	}

	return out, tx.Commit(ctx)
}

func (s *store) replayDomain(ctx context.Context, domain string, batchSize int, handle func([]urlRecord) error) (int64, error) {
	var domainID int64
	err := s.pool.QueryRow(ctx, `SELECT id FROM domains WHERE domain = $1`, domain).Scan(&domainID)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return 0, nil
		}
		return 0, err
	}

	rows, err := s.pool.Query(ctx, `
SELECT id, domain_id, url, COALESCE(last_crawl_run_id, 0)
FROM urls
WHERE domain_id = $1
ORDER BY id
`, domainID)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	batch := make([]urlRecord, 0, batchSize)
	var total int64
	for rows.Next() {
		var record urlRecord
		if err := rows.Scan(&record.ID, &record.DomainID, &record.URL, &record.LastCrawlRunID); err != nil {
			return total, err
		}
		batch = append(batch, record)
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
