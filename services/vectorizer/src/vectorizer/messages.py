from __future__ import annotations

import json

from vectorizer.models import ParserMessage


def parse_message(raw: str | bytes) -> ParserMessage:
    payload = json.loads(raw)
    if payload.get("type") != "vectorize.request.v1" or int(payload.get("version", 0)) != 1:
        raise ValueError(f"unexpected message type/version: {payload.get('type')} v{payload.get('version')}")
    body = payload.get("payload") or {}
    return ParserMessage(
        document_id=int(body["document_id"]),
        version_id=int(body["version_id"]),
    )
