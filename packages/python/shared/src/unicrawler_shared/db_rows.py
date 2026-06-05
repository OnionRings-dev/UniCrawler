from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DocumentVersionRow:
    document_id: int
    version_id: int
    url: str
    domain: str
    markdown: str
    content_hash: str
    document_type: str
    parsed_at: datetime
