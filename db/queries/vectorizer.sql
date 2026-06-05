-- name: GetDocumentVersionForVectorize :one
SELECT
	d.id AS document_id,
	v.id AS version_id,
	d.url,
	domains.domain,
	v.title,
	v.language,
	v.markdown,
	encode(v.content_hash, 'hex') AS content_hash,
	v.status_code,
	v.content_type,
	v.final_url,
	v.document_type,
	v.source_url,
	v.parsed_at
FROM page_document_versions v
JOIN page_documents d ON d.id = v.document_id
JOIN domains ON domains.id = d.domain_id
WHERE d.id = $1
  AND v.id = $2
LIMIT 1;
