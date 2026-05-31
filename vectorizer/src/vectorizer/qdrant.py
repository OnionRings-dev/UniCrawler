from __future__ import annotations

import re
import uuid
from datetime import datetime
from urllib.parse import urlparse

from qdrant_client import QdrantClient, models

from vectorizer.embeddings import Embedder, batched
from vectorizer.models import Chunk, DocumentVersion

COLLECTION_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
POINT_NAMESPACE = uuid.UUID("f548adfb-5273-4e28-bd11-458b287c8e12")


class QdrantSink:
    def __init__(
        self,
        url: str,
        api_key: str | None,
        timeout: int,
        collection_prefix: str,
        collection_scope: str,
        embedder: Embedder,
        batch_size: int,
    ) -> None:
        if url == ":memory:":
            self.client = QdrantClient(location=":memory:")
        else:
            self.client = QdrantClient(url=url, api_key=api_key, timeout=timeout)
        self.collection_prefix = collection_prefix
        self.collection_scope = collection_scope
        self.embedder = embedder
        self.batch_size = batch_size
        self._dimension = embedder.dimension()
        self._ready_collections: set[str] = set()

    def upsert_document(self, document: DocumentVersion, chunks: list[Chunk]) -> str:
        collection = self.collection_for(document)
        self.ensure_collection(collection)

        for chunk_batch in batched(chunks, self.batch_size):
            texts = [chunk.text for chunk in chunk_batch]
            vectors = self.embedder.embed(texts)
            points = [
                models.PointStruct(
                    id=point_id(collection, document, chunk),
                    vector=vector,
                    payload=payload_for(document, chunk, self.embedder.model),
                )
                for chunk, vector in zip(chunk_batch, vectors, strict=True)
            ]
            self.client.upsert(collection_name=collection, points=points, wait=True)

        self.delete_stale_versions(collection, document)
        return collection

    def collection_for(self, document: DocumentVersion) -> str:
        if self.collection_scope == "document_type":
            endpoint = f"{document.domain}_{document.document_type}"
        elif self.collection_scope == "host":
            endpoint = urlparse(document.final_url or document.url).hostname or document.domain
        else:
            endpoint = document.domain
        return collection_name(self.collection_prefix, endpoint)

    def ensure_collection(self, name: str) -> None:
        if name in self._ready_collections:
            return
        if not self.client.collection_exists(name):
            self.client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=self._dimension,
                    distance=models.Distance.COSINE,
                ),
                optimizers_config=models.OptimizersConfigDiff(default_segment_number=2),
            )
            self.create_payload_indexes(name)
            self._ready_collections.add(name)
            return

        info = self.client.get_collection(name)
        vectors = info.config.params.vectors
        size = vectors.size if isinstance(vectors, models.VectorParams) else None
        if size is not None and size != self._dimension:
            raise ValueError(
                f"collection {name} has vector size {size}, expected {self._dimension}"
            )
        self.create_payload_indexes(name)
        self._ready_collections.add(name)

    def create_payload_indexes(self, name: str) -> None:
        for field in ("document_id", "content_hash", "url", "document_type"):
            try:
                schema = models.PayloadSchemaType.INTEGER
                if field != "document_id":
                    schema = models.PayloadSchemaType.KEYWORD
                self.client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=schema,
                    wait=True,
                )
            except Exception:
                pass

    def delete_stale_versions(self, collection: str, document: DocumentVersion) -> None:
        self.client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document.document_id),
                        )
                    ],
                    must_not=[
                        models.FieldCondition(
                            key="content_hash",
                            match=models.MatchValue(value=document.content_hash),
                        )
                    ],
                )
            ),
            wait=True,
        )


def collection_name(prefix: str, endpoint: str) -> str:
    raw = f"{prefix}_{endpoint}".strip().lower()
    name = COLLECTION_SAFE_RE.sub("_", raw).strip("_")
    if len(name) <= 180:
        return name
    suffix = uuid.uuid5(POINT_NAMESPACE, name).hex[:12]
    return f"{name[:167]}_{suffix}"


def point_id(collection: str, document: DocumentVersion, chunk: Chunk) -> str:
    raw = f"{collection}:{document.document_id}:{document.content_hash}:{chunk.index}"
    return str(uuid.uuid5(POINT_NAMESPACE, raw))


def payload_for(document: DocumentVersion, chunk: Chunk, embedding_model: str) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "version_id": document.version_id,
        "content_hash": document.content_hash,
        "chunk_id": chunk.index,
        "chunk_hash": chunk.content_hash,
        "chunk_tokens": chunk.token_count,
        "chunk_start_token": chunk.start_token,
        "chunk_end_token": chunk.end_token,
        "text": chunk.text,
        "headings": list(chunk.headings),
        "url": document.url,
        "final_url": document.final_url,
        "domain": document.domain,
        "title": document.title,
        "language": document.language,
        "document_type": document.document_type,
        "source_url": document.source_url,
        "status_code": document.status_code,
        "content_type": document.content_type,
        "parsed_at": isoformat(document.parsed_at),
        "embedding_model": embedding_model,
    }


def isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
