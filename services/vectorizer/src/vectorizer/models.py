from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ParserMessage:
    document_id: int
    version_id: int


@dataclass(frozen=True)
class DocumentVersion:
    document_id: int
    version_id: int
    url: str
    domain: str
    title: str | None
    language: str | None
    markdown: str
    content_hash: str
    status_code: int | None
    content_type: str | None
    final_url: str | None
    document_type: str
    source_url: str | None
    parsed_at: datetime


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    token_count: int
    start_token: int
    end_token: int
    headings: tuple[str, ...]
    content_hash: str
    kind: str = "content"
    source_chunk_start: int | None = None
    source_chunk_end: int | None = None
