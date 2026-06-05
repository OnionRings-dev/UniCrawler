-- name: ListDomains :many
SELECT id, domain, created_at, updated_at
FROM domains
ORDER BY updated_at DESC, id DESC
LIMIT $1 OFFSET $2;

-- name: ListRecentRuns :many
SELECT cr.id, d.domain, cr.seed_url, cr.status, cr.started_at, cr.finished_at,
       cr.discovered_count, cr.page_count, cr.error_count
FROM crawl_runs cr
JOIN domains d ON d.id = cr.domain_id
ORDER BY cr.started_at DESC
LIMIT $1;
