-- name: UpsertDomain :one
INSERT INTO domains (domain, created_at, updated_at)
VALUES ($1, now(), now())
ON CONFLICT (domain)
DO UPDATE SET updated_at = now()
RETURNING id, domain;

-- name: StartCrawlRun :one
INSERT INTO crawl_runs (domain_id, seed_url, status)
VALUES ($1, $2, 'running')
RETURNING id;

-- name: FinishCrawlRun :exec
UPDATE crawl_runs
SET status = $2,
	finished_at = now(),
	discovered_count = $3,
	page_count = $4,
	error_count = $5
WHERE id = $1;

-- name: GetDomainIDByDomain :one
SELECT id FROM domains WHERE domain = $1;

-- name: ListURLsForDomain :many
SELECT id, domain_id, url, last_crawl_run_id
FROM urls
WHERE domain_id = $1
ORDER BY id;

-- name: InsertPipelineEvent :exec
INSERT INTO pipeline_events (service, event_type, domain_id, crawl_run_id, url_id, document_id, version_id, payload)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
