-- name: GetURLForParse :one
SELECT u.id, u.domain_id, d.domain, u.url, u.url_hash, u.last_crawl_run_id
FROM urls u
JOIN domains d ON d.id = u.domain_id
WHERE u.id = $1;

-- name: LookupURLID :one
SELECT id FROM urls WHERE domain_id = $1 AND url_hash = $2;

-- name: InsertParseError :exec
INSERT INTO page_parse_errors (domain_id, url, url_hash, error, created_at)
VALUES ($1, $2, $3, $4, now());

-- name: UpdateDocumentError :exec
UPDATE page_documents
SET last_error = $3,
	last_parsed_at = now(),
	updated_at = now()
WHERE domain_id = $1 AND url_hash = $2;
