from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from rag_demo.config import Settings
from rag_demo.embeddings import Embedder


@dataclass(frozen=True)
class RetrievalHit:
    collection: str
    point_id: str
    score: float
    payload: dict[str, Any]

    def to_agent_dict(self) -> dict[str, Any]:
        payload = self.payload
        return {
            "collection": self.collection,
            "point_id": self.point_id,
            "score": round(self.score, 4),
            "title": payload.get("title"),
            "url": payload.get("final_url") or payload.get("url"),
            "domain": payload.get("domain"),
            "document_type": payload.get("document_type"),
            "chunk_kind": payload.get("chunk_kind"),
            "chunk_id": payload.get("chunk_id"),
            "headings": payload.get("headings") or [],
            "text": payload.get("text"),
        }


class QdrantRetriever:
    def __init__(self, settings: Settings, embedder: Embedder) -> None:
        self.settings = settings
        self.embedder = embedder
        self.client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=settings.qdrant_timeout,
        )

    def collections(self) -> list[str]:
        configured = self.settings.collections
        if configured:
            return configured

        collections = self.client.get_collections().collections
        prefix = f"{self.settings.collection_prefix}_"
        return sorted(collection.name for collection in collections if collection.name.startswith(prefix))

    def collection_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for name in self.collections():
            info = self.client.get_collection(name)
            summaries.append(
                {
                    "name": name,
                    "points": info.points_count,
                    "indexed_vectors": info.indexed_vectors_count,
                    "status": str(info.status),
                }
            )
        return summaries

    def search(
        self,
        query: str,
        collection: str | None = None,
        domain: str | None = None,
        document_type: str | None = None,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        total_limit = limit if limit is not None else self.settings.retrieval_limit
        collections = [collection] if collection else self.collections()
        if not collections:
            return []

        query_vector = self.embedder.embed([query])[0]
        filters = self._payload_filter(domain=domain, document_type=document_type)
        hits: list[RetrievalHit] = []

        for collection_name in collections:
            response = self.client.query_points(
                collection_name=collection_name,
                query=query_vector,
                query_filter=filters,
                limit=total_limit,
                with_payload=True,
            )
            for point in response.points:
                hits.append(
                    RetrievalHit(
                        collection=collection_name,
                        point_id=str(point.id),
                        score=float(point.score),
                        payload=dict(point.payload or {}),
                    )
                )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:total_limit]

    def get_payload(self, collection: str, point_id: str) -> dict[str, Any] | None:
        points = self.client.retrieve(
            collection_name=collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        return dict(points[0].payload or {})

    def _payload_filter(
        self,
        domain: str | None = None,
        document_type: str | None = None,
    ) -> models.Filter | None:
        must: list[models.FieldCondition] = []
        if domain:
            must.append(models.FieldCondition(key="domain", match=models.MatchValue(value=domain)))
        if document_type:
            must.append(
                models.FieldCondition(
                    key="document_type",
                    match=models.MatchValue(value=document_type),
                )
            )
        if not must:
            return None
        return models.Filter(must=must)


def dumps_for_tool(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
