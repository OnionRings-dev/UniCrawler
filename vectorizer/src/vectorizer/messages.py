from __future__ import annotations

import json
from datetime import datetime

from vectorizer.models import ParserMessage


def parse_message(raw: str | bytes) -> ParserMessage:
    payload = json.loads(raw)
    return ParserMessage(
        url=str(payload["url"]),
        domain=str(payload["domain"]),
        document_id=int(payload["document_id"]),
        content_hash=str(payload["content_hash"]),
        parsed_at=parse_datetime(str(payload["parsed_at"])),
        document_type=str(payload.get("document_type") or "html"),
        source_url=payload.get("source_url") or None,
    )


def parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)

