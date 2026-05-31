from __future__ import annotations

from contextlib import AbstractContextManager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from vectorizer.models import DocumentVersion, ParserMessage


class PostgresStore(AbstractContextManager["PostgresStore"]):
    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=False)

    def __enter__(self) -> "PostgresStore":
        self._pool.open(wait=True)
        return self

    def __exit__(self, *args: object) -> None:
        self._pool.close()

    def get_document_version(self, message: ParserMessage) -> DocumentVersion | None:
        with self._pool.connection() as conn:
            return fetch_document_version(conn, message)


def fetch_document_version(
    conn: Connection[dict[str, object]],
    message: ParserMessage,
) -> DocumentVersion | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
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
            WHERE d.id = %s
              AND encode(v.content_hash, 'hex') = %s
            LIMIT 1
            """,
            (message.document_id, message.content_hash),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return DocumentVersion(
        document_id=int(row["document_id"]),
        version_id=int(row["version_id"]),
        url=str(row["url"]),
        domain=str(row["domain"]),
        title=optional_str(row["title"]),
        language=optional_str(row["language"]),
        markdown=str(row["markdown"]),
        content_hash=str(row["content_hash"]),
        status_code=optional_int(row["status_code"]),
        content_type=optional_str(row["content_type"]),
        final_url=optional_str(row["final_url"]),
        document_type=str(row["document_type"] or "html"),
        source_url=optional_str(row["source_url"]),
        parsed_at=row["parsed_at"],  # type: ignore[arg-type]
    )


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)

